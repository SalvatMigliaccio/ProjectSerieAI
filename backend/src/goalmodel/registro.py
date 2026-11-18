"""
Il track record: il registro delle previsioni e l'archivio delle giornate chiuse.

L'UNICO PUNTO CHE SA DOVE STA (ADR 0007, fase 2). Fino al 18 novembre 2026 il
track record era fatto di file in `track_record/`, scritti solo da processi
locali e versionati in git (ADR 0002, punto 1). Da qui puo' stare in
Postgres, perche' il sito deployato deve funzionare senza un PC acceso e
senza un `git push` dopo ogni giornata.

STESSA VARIABILE DEI DATI, `GOALMODEL_STORAGE`, stesse tre modalita':

  parquet   i file, come sempre. Nessun accesso al database.
  doppio    scrive file E database, legge i file. Il database che non
            risponde non ferma la pipeline: il file e' gia' scritto.
  postgres  solo il database. I file restano come archivio congelato.

LA GARANZIA PASSA DAL PROCESSO AL DATABASE. Con i file valeva perche' li
scriveva solo un processo locale e `git log` ne teneva la storia. In Postgres
la impongono i trigger, che valgono anche per il proprietario delle tabelle e
quindi anche per un bug dell'applicazione:

  - append-only: `UPDATE`, `DELETE` e `TRUNCATE` sollevano, su entrambe le
    tabelle. Solo un `DROP TRIGGER` deliberato le riapre, e lascia traccia
    nella catena (sotto);
  - l'ora della previsione la mette il server (`clock_timestamp()`), mai il
    client: non si puo' retrodatare una riga;
  - una previsione con il calcio d'inizio gia' passato, o sconosciuto, viene
    rifiutata dal database, non solo dall'assert di `predict.py`;
  - ogni riga porta l'impronta SHA-256 della precedente. Una riga cambiata o
    tolta da chi ha spento i trigger rompe la catena, e `verifica()` lo dice.

LO STORICO SI IMPORTA UNA VOLTA. Le 127 righe scritte su file prima del
passaggio hanno ora e calcio d'inizio nel passato: il trigger le accetta solo
con `origine = 'import'` e solo finche' nel registro non c'e' nessuna riga
`live`. Alla prima previsione vera quella porta si chiude per sempre.
`verifica()` confronta le righe importate con i CSV, che restano in git.

PERCHE' QUI E NON IN ALEMBIC: come per `pg.prepara`, `goalmodel` non importa
`backend`. Lo schema si crea in modo idempotente alla prima scrittura.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, storage

log = logging.getLogger("registro")

SCHEMA = "track_record"

FILE_REGISTRO = config.TRACK_RECORD / "predictions_log.csv"
CARTELLA_GIORNATE = config.TRACK_RECORD / "rounds"

# Le colonne del registro, nell'ordine del CSV: chi legge non deve sapere da
# dove arrivano.
COLONNE_REGISTRO = [
    "timestamp_prediction", "model_version",
    "league", "season", "matchday", "match_date", "home_team", "away_team",
    "lambda_home", "lambda_away",
    "p_home", "p_draw", "p_away", "p_over25", "p_btts",
    "odds_home", "odds_draw", "odds_away", "odds_source",
]

# Le colonne di un file di giornata (close_round.COLONNE), ricopiate qui di
# proposito: e' lo schema di una tabella, e cambiarle e' una migrazione.
COLONNE_GIORNATA = [
    "season", "matchday", "match_date", "home_team", "away_team",
    "model_version", "timestamp_prediction",
    "p_home", "p_draw", "p_away", "p_over25", "p_btts",
    "lambda_home", "lambda_away",
    "odds_home", "odds_draw", "odds_away", "odds_source",
    "FTHG", "FTAG", "FTR", "fonte_risultato", "esito_previsto", "azzeccato",
    "rps", "log_loss", "brier", "rps_mercato", "valida",
]

_NUMERICHE = ["lambda_home", "lambda_away", "p_home", "p_draw", "p_away",
              "p_over25", "p_btts", "odds_home", "odds_draw", "odds_away"]
_TESTO_G = {"season", "match_date", "home_team", "away_team", "model_version",
            "timestamp_prediction", "odds_source", "FTR", "fonte_risultato",
            "esito_previsto"}


class RegistroRifiutato(ValueError):
    """Il database ha rifiutato una scrittura: append-only, fischio, import chiuso."""


# ---------------------------------------------------------------------------
# Dove
# ---------------------------------------------------------------------------

def modalita() -> str:
    return storage.modalita()


def su_file() -> bool:
    return modalita() in ("parquet", "doppio")


def su_db() -> bool:
    return modalita() in ("doppio", "postgres")


def legge_db() -> bool:
    return modalita() == "postgres"


def _s() -> str:
    from . import pg
    return pg._q(pg.prefisso() + SCHEMA)


def _vietato() -> None:
    if storage._vietato is not None:
        raise storage.ScritturaVietata(f"scrittura nel track record rifiutata: {storage._vietato}")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _ddl(s: str) -> list[str]:
    """
    Tabelle, impronta e trigger. Idempotente: si rilancia a ogni scrittura.

    `numeric` per lambda, probabilita' e quote: sono gia' arrotondati a 4 e 5
    cifre quando si registrano, e la rappresentazione testuale di un numeric
    e' esatta — e' quella che entra nell'impronta, che deve essere la stessa
    oggi e fra cinque anni su un'altra macchina.
    """
    num = ", ".join(f"{c} numeric NOT NULL" for c in _NUMERICHE[:7])
    return [
        f"CREATE SCHEMA IF NOT EXISTS {s}",
        f"""CREATE TABLE IF NOT EXISTS {s}.previsioni (
            id            bigserial PRIMARY KEY,
            registrata_il timestamptz NOT NULL DEFAULT clock_timestamp(),
            origine       text NOT NULL DEFAULT 'live' CHECK (origine IN ('live', 'import')),
            model_version text NOT NULL,
            league        text NOT NULL,
            season        text NOT NULL,
            matchday      integer NOT NULL,
            match_date    date NOT NULL,
            home_team     text NOT NULL,
            away_team     text NOT NULL,
            kickoff_utc   timestamptz,
            {num},
            odds_home     numeric,
            odds_draw     numeric,
            odds_away     numeric,
            odds_source   text,
            prev_hash     text,
            row_hash      text NOT NULL,
            UNIQUE (model_version, league, season, home_team, away_team),
            CHECK (origine = 'import' OR kickoff_utc IS NOT NULL)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {s}.giornate (
            id                   bigserial PRIMARY KEY,
            archiviata_il        timestamptz NOT NULL DEFAULT clock_timestamp(),
            season               text NOT NULL,
            matchday             integer NOT NULL,
            match_date           text,
            home_team            text NOT NULL,
            away_team            text NOT NULL,
            model_version        text NOT NULL,
            timestamp_prediction text,
            p_home double precision, p_draw double precision, p_away double precision,
            p_over25 double precision, p_btts double precision,
            lambda_home double precision, lambda_away double precision,
            odds_home double precision, odds_draw double precision, odds_away double precision,
            odds_source          text,
            "FTHG" double precision, "FTAG" double precision,
            "FTR"                text,
            fonte_risultato      text,
            esito_previsto       text,
            azzeccato double precision, rps double precision, log_loss double precision,
            brier double precision, rps_mercato double precision,
            valida               boolean NOT NULL,
            UNIQUE (season, matchday, home_team, away_team, model_version)
        )""",
        # L'impronta di una riga, concatenata a quella della precedente. Ogni
        # campo e' scritto in una forma che non dipende da DateStyle ne' da
        # extra_float_digits, e i nulli sono stringhe vuote: concat_ws salta i
        # NULL, e due righe diverse darebbero la stessa stringa.
        f"""CREATE OR REPLACE FUNCTION {s}.impronta(prev text, r {s}.previsioni)
            RETURNS text LANGUAGE sql STABLE AS $$
            SELECT encode(sha256(convert_to(concat_ws('|',
                coalesce(prev, ''),
                to_char(r.registrata_il AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                r.origine, r.model_version, r.league, r.season, r.matchday::text,
                to_char(r.match_date, 'YYYY-MM-DD'), r.home_team, r.away_team,
                coalesce(to_char(r.kickoff_utc AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'), ''),
                r.lambda_home::text, r.lambda_away::text, r.p_home::text, r.p_draw::text,
                r.p_away::text, r.p_over25::text, r.p_btts::text,
                coalesce(r.odds_home::text, ''), coalesce(r.odds_draw::text, ''),
                coalesce(r.odds_away::text, ''), coalesce(r.odds_source, '')
            ), 'UTF8')), 'hex')
            $$""",
        f"""CREATE OR REPLACE FUNCTION {s}.previsioni_prima() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE prev text;
            BEGIN
              IF NEW.origine = 'live' THEN
                -- L'ora e' del server. Qualunque valore mandato dal client
                -- viene sovrascritto: una riga non si retrodata.
                NEW.registrata_il := clock_timestamp();
                IF NEW.kickoff_utc IS NULL OR NEW.kickoff_utc <= NEW.registrata_il THEN
                  RAISE EXCEPTION 'previsione rifiutata: %-% non ha un calcio d''inizio futuro (%)',
                    NEW.home_team, NEW.away_team, NEW.kickoff_utc
                    USING ERRCODE = 'check_violation';
                END IF;
              ELSIF EXISTS (SELECT 1 FROM {s}.previsioni WHERE origine = 'live') THEN
                RAISE EXCEPTION 'import chiuso: il registro ha gia'' previsioni live'
                  USING ERRCODE = 'check_violation';
              END IF;
              -- Uno scrittore alla volta: la catena ha un solo ultimo anello.
              PERFORM pg_advisory_xact_lock(hashtext('{s}.previsioni'));
              SELECT row_hash INTO prev FROM {s}.previsioni ORDER BY id DESC LIMIT 1;
              NEW.prev_hash := prev;
              NEW.row_hash := {s}.impronta(prev, NEW);
              RETURN NEW;
            END $$""",
        f"""CREATE OR REPLACE FUNCTION {s}.solo_aggiunte() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'il track record e'' append-only: % rifiutato su %',
                TG_OP, TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
            END $$""",
    ]


_TRIGGER = [
    ("previsioni", "previsioni_prima", "BEFORE INSERT", "ROW", "previsioni_prima"),
    ("previsioni", "previsioni_solo_aggiunte", "BEFORE UPDATE OR DELETE", "ROW", "solo_aggiunte"),
    ("previsioni", "previsioni_no_truncate", "BEFORE TRUNCATE", "STATEMENT", "solo_aggiunte"),
    ("giornate", "giornate_solo_aggiunte", "BEFORE UPDATE OR DELETE", "ROW", "solo_aggiunte"),
    ("giornate", "giornate_no_truncate", "BEFORE TRUNCATE", "STATEMENT", "solo_aggiunte"),
]


def prepara(cur) -> None:
    """Crea cio' che manca. I trigger si creano solo se assenti: mai un DROP."""
    from . import pg
    s = _s()
    for istruzione in _ddl(s):
        cur.execute(istruzione)
    nome_schema = pg.prefisso() + SCHEMA
    for tabella, nome, quando, livello, funzione in _TRIGGER:
        cur.execute(
            "SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s AND t.tgname = %s",
            (nome_schema, tabella, nome))
        if cur.fetchone() is None:
            cur.execute(f"CREATE TRIGGER {nome} {quando} ON {s}.{tabella} "
                        f"FOR EACH {livello} EXECUTE FUNCTION {s}.{funzione}()")


def _connessione():
    from . import pg
    return pg.connessione()


def _rifiuto(exc: Exception) -> RegistroRifiutato | None:
    """Un errore del database che e' una regola del registro, non un guasto."""
    import psycopg
    if isinstance(exc, (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege,
                        psycopg.errors.RaiseException)):
        return RegistroRifiutato(str(exc).splitlines()[0])
    return None


# ---------------------------------------------------------------------------
# Registro: scrittura
# ---------------------------------------------------------------------------

def _valore(v):
    """Un valore pandas come lo vuole psycopg: None al posto di NaN e NaT."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def _decimale(v):
    """Un float come numeric ESATTO del suo testo: 0.59118 e non 0.5911799999..."""
    from decimal import Decimal
    v = _valore(v)
    return None if v is None else Decimal(repr(float(v)))


def aggiungi(rec: pd.DataFrame, kickoff: pd.Series) -> int:
    """
    Inserisce le righe del registro come previsioni `live`. Quante sono entrate.

    `rec` ha le colonne del CSV; `timestamp_prediction` viene IGNORATO, perche'
    l'ora la mette il database. Una partita gia' registrata per lo stesso
    modello non entra (`ON CONFLICT DO NOTHING`): e' la stessa idempotenza di
    `predict.append_log`, imposta dal vincolo invece che da una lettura prima.
    """
    _vietato()
    if rec.empty:
        return 0
    s = _s()
    inserite = 0
    try:
        with _connessione() as conn, conn.cursor() as cur:
            prepara(cur)
            for (_, r), ko in zip(rec.iterrows(), kickoff, strict=True):
                cur.execute(
                    f"""INSERT INTO {s}.previsioni (origine, model_version, league, season,
                        matchday, match_date, home_team, away_team, kickoff_utc,
                        lambda_home, lambda_away, p_home, p_draw, p_away, p_over25, p_btts,
                        odds_home, odds_draw, odds_away, odds_source)
                        VALUES ('live', %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING RETURNING id""",
                    (r["model_version"], r["league"], str(r["season"]), int(r["matchday"]),
                     str(r["match_date"]), r["home_team"], r["away_team"], _valore(pd.Timestamp(ko)),
                     *[_decimale(r[c]) for c in _NUMERICHE], _valore(r["odds_source"])))
                inserite += cur.fetchone() is not None
    except Exception as exc:
        rifiuto = _rifiuto(exc)
        if rifiuto is not None:
            raise rifiuto from exc
        raise
    log.info("registro in Postgres: %d previsioni nuove", inserite)
    return inserite


# ---------------------------------------------------------------------------
# Registro: lettura
# ---------------------------------------------------------------------------

def _leggi_db_registro() -> pd.DataFrame:
    s = _s()
    with _connessione() as conn, conn.cursor() as cur:
        prepara(cur)
        cur.execute(f"""SELECT to_char(registrata_il AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                        model_version, league, season, matchday,
                        to_char(match_date, 'YYYY-MM-DD'), home_team, away_team,
                        {", ".join(_NUMERICHE)}, odds_source
                        FROM {s}.previsioni ORDER BY id""")
        righe = cur.fetchall()
    df = pd.DataFrame(righe, columns=COLONNE_REGISTRO)
    for c in _NUMERICHE:
        df[c] = pd.to_numeric(df[c].map(lambda v: None if v is None else float(v)))
    df["matchday"] = df["matchday"].astype("int64")
    return df


def leggi(path: Path = FILE_REGISTRO) -> pd.DataFrame:
    """
    Il registro grezzo, come `pd.read_csv` lo darebbe: tutte le righe.

    Dal database solo in modalita' `postgres` e solo per il registro vero: un
    `path` diverso e' un test o il backfill, che restano file.
    """
    if legge_db() and Path(path) == FILE_REGISTRO:
        return _leggi_db_registro()
    return pd.read_csv(path)


def esiste(path: Path = FILE_REGISTRO) -> bool:
    if legge_db() and Path(path) == FILE_REGISTRO:
        try:
            return not _leggi_db_registro().empty
        except Exception as exc:  # noqa: BLE001 - una vista di stato non cade
            log.warning("registro in Postgres non leggibile: %s", exc)
            return False
    return Path(path).exists()


# ---------------------------------------------------------------------------
# Giornate
# ---------------------------------------------------------------------------

def nome_file_giornata(season: str, matchday: int) -> str:
    return f"round_{season}_{int(matchday):02d}.csv"


def giornata_chiusa(season: str, matchday: int, cartella: Path | None = None) -> bool:
    """Lo stato CHIUSA: il file, o le righe nell'archivio in Postgres."""
    if legge_db():
        s = _s()
        with _connessione() as conn, conn.cursor() as cur:
            prepara(cur)
            cur.execute(f"SELECT 1 FROM {s}.giornate WHERE season = %s AND matchday = %s LIMIT 1",
                        (str(season), int(matchday)))
            return cur.fetchone() is not None
    return ((cartella or CARTELLA_GIORNATE) / nome_file_giornata(season, matchday)).exists()


def _inserisci_giornata(cur, d: pd.DataFrame) -> int:
    s = _s()
    colonne = ", ".join(f'"{c}"' if c[0].isupper() else c for c in COLONNE_GIORNATA)
    segnaposti = ", ".join(["%s"] * len(COLONNE_GIORNATA))
    n = 0
    for _, r in d.iterrows():
        valori = []
        for c in COLONNE_GIORNATA:
            v = _valore(r.get(c))
            if c == "valida":
                v = str(v).lower() in ("true", "1", "1.0") if v is not None else True
            elif c in _TESTO_G and v is not None:
                v = str(v)
            elif c == "matchday":
                v = int(v)
            valori.append(v)
        cur.execute(f"INSERT INTO {s}.giornate ({colonne}) VALUES ({segnaposti}) "
                    f"ON CONFLICT DO NOTHING RETURNING id", valori)
        n += cur.fetchone() is not None
    return n


def archivia_giornata(d: pd.DataFrame, season: str, matchday: int,
                      cartella: Path | None = None) -> None:
    """
    Scrive l'archivio di una giornata, una volta sola, dove la modalita' dice.

    Il chiamante ha gia' controllato che non fosse chiusa. Il database lo
    ricontrolla da solo: l'archivio e' append-only e unico per partita.
    """
    _vietato()
    if su_file():
        cartella = cartella or CARTELLA_GIORNATE
        cartella.mkdir(parents=True, exist_ok=True)
        d.to_csv(cartella / nome_file_giornata(season, matchday), index=False)
    if not su_db():
        return
    try:
        with _connessione() as conn, conn.cursor() as cur:
            prepara(cur)
            _inserisci_giornata(cur, d.reindex(columns=COLONNE_GIORNATA))
    except Exception as exc:
        if modalita() == "doppio":
            log.error("doppio: giornata %s/%s archiviata su file ma NON in Postgres (%s)",
                      season, matchday, exc)
            return
        rifiuto = _rifiuto(exc)
        if rifiuto is not None:
            raise rifiuto from exc
        raise


def _leggi_db_giornate() -> pd.DataFrame:
    s = _s()
    colonne = ", ".join(f'"{c}"' if c[0].isupper() else c for c in COLONNE_GIORNATA)
    with _connessione() as conn, conn.cursor() as cur:
        prepara(cur)
        cur.execute(f"SELECT {colonne} FROM {s}.giornate ORDER BY season, matchday, id")
        righe = cur.fetchall()
    df = pd.DataFrame(righe, columns=COLONNE_GIORNATA)
    df["source_file"] = [nome_file_giornata(s_, m) for s_, m in zip(df["season"], df["matchday"])]
    return df


def leggi_giornate(cartella: Path | None = None) -> pd.DataFrame:
    """
    Tutte le giornate chiuse, concatenate, con `source_file` per riga.

    `source_file` e' il nome del file di giornata anche quando la riga arriva
    dal database: e' l'identita' della giornata per chi legge, e cambiarla
    cambierebbe le risposte dell'API.
    """
    if legge_db():
        return _leggi_db_giornate()
    return leggi_giornate_da_file(cartella)


def calcola_riepilogo(giornate: pd.DataFrame) -> pd.DataFrame:
    """
    Il riepilogo cumulativo, ricalcolato da zero dalle giornate.

    Era in `close_round.aggiorna_riepilogo`; sta qui perche' serve anche a chi
    legge dal database, dove `riepilogo.csv` non esiste: e' una vista, si
    ricalcola, non si archivia.
    """
    if giornate.empty:
        return pd.DataFrame()
    righe = []
    for f, d in giornate.groupby("source_file", sort=True):
        if "valida" in d.columns:
            ok = d["valida"].astype(str).str.lower().isin(("true", "1", "1.0"))
            valide = d[ok]
        else:
            valide = d
        righe.append({
            "season": str(d["season"].iloc[0]),
            "matchday": int(d["matchday"].iloc[0]),
            "n_previsioni": len(d),
            "n_valide": len(valide),
            "rps": float(valide["rps"].mean()) if len(valide) else np.nan,
            "rps_mercato": float(valide["rps_mercato"].mean()) if len(valide) else np.nan,
            "accuratezza": float(valide["azzeccato"].mean()) if len(valide) else np.nan,
            "file": f,
        })
    tab = pd.DataFrame(righe).sort_values(["season", "matchday"]).reset_index(drop=True)
    # Cumulativo pesato sulle partite, non media di medie: una giornata con
    # tre previsioni valide non deve pesare quanto una da dieci.
    tab["n_cumulate"] = tab["n_valide"].cumsum()
    tab["rps_cumulativo"] = (tab["rps"] * tab["n_valide"]).cumsum() / tab["n_valide"].cumsum()
    return tab


# ---------------------------------------------------------------------------
# Import, verifica, versione
# ---------------------------------------------------------------------------

def importa() -> dict[str, int]:
    """
    Porta i CSV in Postgres: registro con `origine = 'import'`, e giornate.

    Idempotente: cio' che c'e' gia' non si duplica. Il registro si importa solo
    finche' non esiste una riga `live`; dopo, il database rifiuta (e questo e'
    il punto). Le righe entrano nell'ordine dei tempi del file, cosi' la
    catena delle impronte segue la storia vera.
    """
    _vietato()
    s = _s()
    esito = {"previsioni": 0, "giornate": 0}
    with _connessione() as conn, conn.cursor() as cur:
        prepara(cur)
        if FILE_REGISTRO.exists():
            reg = pd.read_csv(FILE_REGISTRO)
            reg["_t"] = pd.to_datetime(reg["timestamp_prediction"], utc=True)
            reg = reg.sort_values("_t", kind="stable")
            for _, r in reg.iterrows():
                try:
                    cur.execute(
                        f"""INSERT INTO {s}.previsioni (registrata_il, origine, model_version,
                            league, season, matchday, match_date, home_team, away_team,
                            lambda_home, lambda_away, p_home, p_draw, p_away, p_over25, p_btts,
                            odds_home, odds_draw, odds_away, odds_source)
                            VALUES (%s, 'import', %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING RETURNING id""",
                        (r["_t"].to_pydatetime(), r["model_version"], r["league"],
                         str(r["season"]), int(r["matchday"]), str(r["match_date"]),
                         r["home_team"], r["away_team"],
                         *[_decimale(r[c]) for c in _NUMERICHE], _valore(r["odds_source"])))
                except Exception as exc:
                    rifiuto = _rifiuto(exc)
                    if rifiuto is not None:
                        raise rifiuto from exc
                    raise
                esito["previsioni"] += cur.fetchone() is not None
        g = leggi_giornate_da_file()
        if not g.empty:
            esito["giornate"] = _inserisci_giornata(cur, g.reindex(columns=COLONNE_GIORNATA))
    log.info("importate %d previsioni e %d righe di giornata", esito["previsioni"], esito["giornate"])
    return esito


def leggi_giornate_da_file(cartella: Path | None = None) -> pd.DataFrame:
    """Le giornate dai CSV, qualunque sia la modalita'. Per file, import e verifica."""
    cartella = cartella or CARTELLA_GIORNATE
    if not cartella.is_dir():
        return pd.DataFrame()
    frames = []
    for f in sorted(cartella.glob("round_*.csv")):
        try:
            piece = pd.read_csv(f)
        except (pd.errors.EmptyDataError, OSError) as exc:
            log.warning("file di giornata illeggibile %s: %s", f.name, exc)
            continue
        frames.append(piece.assign(source_file=f.name))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def verifica() -> list[str]:
    """
    Cosa non torna. Lista vuota se tutto e' a posto.

    Tre controlli:
    1. la catena: ogni impronta ricalcolata coincide con quella salvata, e gli
       anelli formano UNA catena (un solo inizio, nessun anello saltato o
       duplicato). Seguita per collegamento, non per id: due scritture
       concorrenti prendono gli id in un ordine e il lucchetto in un altro;
    2. il registro su file e quello in Postgres dicono la stessa cosa:
       stesse partite per modello, stessi numeri. L'ora deve coincidere al
       secondo per le righe importate; per quelle live, scritte in `doppio`,
       file e database prendono l'ora ciascuno dal proprio orologio e possono
       differire di qualche secondo;
    3. le giornate archiviate coincidono con i file di giornata.
    """
    s = _s()
    problemi: list[str] = []
    with _connessione() as conn, conn.cursor() as cur:
        prepara(cur)
        cur.execute(f"SELECT id, prev_hash, row_hash, {s}.impronta(prev_hash, p) = row_hash, origine "
                    f"FROM {s}.previsioni p ORDER BY id")
        righe = cur.fetchall()
    for id_, _, _, ok, _ in righe:
        if not ok:
            problemi.append(f"previsione {id_}: l'impronta non corrisponde al contenuto")
    if righe:
        figli: dict[str | None, list[int]] = {}
        for id_, prev, _, _, _ in righe:
            figli.setdefault(prev, []).append(id_)
        if len(figli.get(None, [])) != 1:
            problemi.append(f"catena: {len(figli.get(None, []))} anelli iniziali invece di 1")
        doppi = [p for p, f in figli.items() if p is not None and len(f) > 1]
        if doppi:
            problemi.append(f"catena: {len(doppi)} anelli con piu' di un successore")
        hashes = {h for _, _, h, _, _ in righe}
        orfani = [p for p in figli if p is not None and p not in hashes]
        if orfani:
            problemi.append(f"catena: {len(orfani)} anelli puntano a righe che non ci sono piu'")

    if FILE_REGISTRO.exists():
        f = pd.read_csv(FILE_REGISTRO)
        d = _leggi_db_registro()
        chiave = ["model_version", "league", "season", "home_team", "away_team"]
        for x in (f, d):
            x["season"] = x["season"].astype(str)
        m = f.merge(d, on=chiave, how="outer", suffixes=("_f", "_d"), indicator=True)
        solo_f = int((m["_merge"] == "left_only").sum())
        solo_d = int((m["_merge"] == "right_only").sum())
        if solo_f:
            problemi.append(f"registro: {solo_f} previsioni nel CSV mancano in Postgres")
        if solo_d:
            problemi.append(f"registro: {solo_d} previsioni in Postgres mancano nel CSV")
        both = m[m["_merge"] == "both"]
        for c in _NUMERICHE:
            a, b = both[f"{c}_f"].astype(float), both[f"{c}_d"].astype(float)
            diversi = ~((a == b) | (a.isna() & b.isna()))
            if diversi.any():
                problemi.append(f"registro: {int(diversi.sum())} righe con {c} diverso")
        ta = pd.to_datetime(both["timestamp_prediction_f"], utc=True)
        tb = pd.to_datetime(both["timestamp_prediction_d"], utc=True)
        scarto = (ta - tb).abs()
        if (scarto > pd.Timedelta(minutes=5)).any():
            problemi.append(f"registro: {int((scarto > pd.Timedelta(minutes=5)).sum())} "
                            f"righe con l'ora diversa di oltre 5 minuti")

    gf, gd = leggi_giornate_da_file(), _leggi_db_giornate()
    if not gf.empty or not gd.empty:
        kf = set(zip(gf.get("source_file", []), gf.get("home_team", []), gf.get("away_team", [])))
        kd = set(zip(gd.get("source_file", []), gd.get("home_team", []), gd.get("away_team", [])))
        if kf != kd:
            problemi.append(f"giornate: {len(kf - kd)} righe solo nei file, "
                            f"{len(kd - kf)} solo in Postgres")
    return problemi


def versione() -> tuple:
    """
    Cosa cambia quando il track record cambia: per la cache dell'API.

    In modalita' file sono gli mtime, e se ne occupa chi chiama. In Postgres
    basta contare e prendere l'ultimo id: le tabelle sono append-only, quindi
    una riga nuova sposta sempre almeno uno dei due.
    """
    if not legge_db():
        return ()
    s = _s()
    try:
        with _connessione() as conn, conn.cursor() as cur:
            prepara(cur)
            cur.execute(f"SELECT (SELECT count(*) || ':' || coalesce(max(id), 0) FROM {s}.previsioni), "
                        f"(SELECT count(*) || ':' || coalesce(max(id), 0) FROM {s}.giornate)")
            return tuple(cur.fetchone())
    except Exception as exc:  # noqa: BLE001 - la cache non deve far cadere l'API
        log.warning("versione del registro non leggibile: %s", exc)
        return ("errore", dt.datetime.now(dt.UTC).isoformat())


def aggiornato_il() -> pd.Timestamp | None:
    """L'ultima scrittura nel track record in Postgres, per `updated_at` dell'API."""
    s = _s()
    with _connessione() as conn, conn.cursor() as cur:
        prepara(cur)
        cur.execute(f"SELECT greatest((SELECT max(registrata_il) FROM {s}.previsioni "
                    f"WHERE origine = 'live'), (SELECT max(archiviata_il) FROM {s}.giornate))")
        valore = cur.fetchone()[0]
    return pd.Timestamp(valore) if valore is not None else None


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="goalmodel registro",
        description="Il track record in Postgres: import dello storico e verifica.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--importa", action="store_true",
                   help="porta i CSV di track_record/ in Postgres (idempotente)")
    g.add_argument("--verifica", action="store_true",
                   help="catena delle impronte, e CSV contro Postgres")
    g.add_argument("--stato", action="store_true", help="quante righe, e dove si legge")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.importa:
        esito = importa()
        print(f"importate {esito['previsioni']} previsioni e {esito['giornate']} righe di giornata")
        problemi = verifica()
    elif args.verifica:
        problemi = verifica()
    else:
        print(f"modalita': {modalita()}  (GOALMODEL_STORAGE)")
        reg = _leggi_db_registro()
        g = _leggi_db_giornate()
        print(f"Postgres: {len(reg)} previsioni, {g['source_file'].nunique() if not g.empty else 0} "
              f"giornate chiuse")
        return 0

    if problemi:
        print("NON COINCIDE:")
        for p in problemi:
            print(f"  - {p}")
        return 1
    print("verifica: catena integra, CSV e Postgres coincidono")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
