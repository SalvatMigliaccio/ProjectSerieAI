"""
Postgres come archivio dei dati del modello (ADR 0002, schema in ADR 0006).

Questo modulo sa una cosa sola: portare un DataFrame in una tabella e
riportarlo indietro **identico bit a bit**. Chi decide QUANDO usarlo e'
`storage.py`; nessun altro modulo dovrebbe importarlo.

COME RESTA IDENTICO. Il dato non viaggia come DataFrame ma come tabella arrow,
la stessa forma intermedia che `to_parquet` e `read_parquet` usano. Lo schema
arrow — con i metadati pandas dentro, cioe' i dtype `Int64`, `Float64`, `str`
— si salva nel catalogo accanto alla tabella. Alla lettura si ricostruisce lo
stesso schema e si chiede ad arrow la stessa conversione verso pandas che fa
il parquet: stessi ingressi, stessa uscita. Verificato su tutti i 32 file di
`data/` il 6 novembre 2026, a livello di bit dei float.

Il trasporto e' CSV via `COPY`, in entrambi i sensi:
  * i float escono da arrow nella rappresentazione piu' corta che torna esatta,
    e Postgres li rilegge esatti; all'uscita `extra_float_digits = 1` (il
    default dalla 12, impostato comunque) fa lo stesso;
  * NULL e' il campo vuoto senza virgolette, la stringa vuota e' `""`: le due
    cose restano distinte, come `NA` e `""` in pandas;
  * NaN resta NaN (`double precision` lo ammette), distinto da NULL: per un
    `Float64` nullable sono due valori diversi, e per LightGBM pure.

COSA NON FA. Non migra lo schema: ogni scrittura sostituisce la tabella
intera, come faceva il parquet. Le colonne seguono il DataFrame, perche' le
fonti cambiano colonne da sole (football-data aggiunge un book, soccerdata una
statistica) e un ALTER scritto a mano per ognuna sarebbe una migrazione
dimenticata alla prima stagione.
"""

from __future__ import annotations

import fnmatch
import io
import json
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pcsv

from . import config, schema

log = logging.getLogger("pg")

VARIABILE_URL = "AI_NAPLES_DATABASE_URL"

# Gli schemi Postgres ricalcano le cartelle di `data/`: un livello, un posto.
# Inventare nomi nuovi ("core", "features") obbligherebbe a una tabella di
# traduzione in piu', e la cartella da cui un file viene e' gia' la sua
# classificazione.
LIVELLI = ("raw", "interim", "processed")
SCHEMA_META = "goalmodel"

# I TEST NON TOCCANO GLI SCHEMI VERI. Con `GOALMODEL_PG_PREFISSO=test_` ogni
# schema diventa `test_raw`, `test_goalmodel`, ...: stesso database, tabelle
# diverse. `tests/conftest.py` lo imposta per ogni test, nello stesso spirito
# della rete sui file — un test che scrive `raw.matches_master` sostituirebbe
# il dataset vero con righe sintetiche, e il catalogo direbbe che e' tutto a
# posto.
VARIABILE_PREFISSO = "GOALMODEL_PG_PREFISSO"
SUFFISSO_NUOVA = "__nuova"

# Postgres tronca in silenzio gli identificatori oltre 63 byte: due colonne
# lunghe con lo stesso prefisso diventerebbero la stessa colonna.
MAX_IDENTIFICATORE = 63
NOME_VALIDO = re.compile(r"^[a-z0-9_]+(/[a-z0-9_]+)?$")


class TipoNonSupportato(TypeError):
    """Un tipo arrow che questo modulo non sa portare in Postgres ESATTO."""


class DatoAssente(FileNotFoundError):
    """Nel catalogo non c'e' quel dato. Stessa famiglia dell'assenza del file."""


# ---------------------------------------------------------------------------
# Connessione
# ---------------------------------------------------------------------------

def url() -> str:
    """
    L'URL del database: la stessa variabile dell'autenticazione.

    Un database solo per account e dati, quindi una variabile sola. Si legge
    dall'ambiente e, in mancanza, dal `.env` della radice, come fa
    `backend/auth/settings.py`: chi lancia la pipeline a mano non deve
    esportarla. `goalmodel` non importa `backend`, quindi il `.env` si legge
    qui, in poche righe, invece che con pydantic-settings.
    """
    valore = os.environ.get(VARIABILE_URL)
    env = config.ROOT / ".env"
    if not valore and env.exists():
        with env.open(encoding="utf-8") as fh:
            for riga in fh:
                chiave, sep, resto = riga.strip().partition("=")
                if sep and chiave.strip() == VARIABILE_URL:
                    valore = resto.strip().strip('"').strip("'")
    if not valore:
        raise RuntimeError(
            f"{VARIABILE_URL} non impostata: serve per leggere e scrivere i dati "
            f"in Postgres. Vedi .env.example, o torna ai file con "
            f"GOALMODEL_STORAGE=parquet."
        )
    # SQLAlchemy vuole il driver nello schema dell'URL, psycopg no.
    return re.sub(r"^postgresql\+psycopg://", "postgresql://", valore)


@contextmanager
def connessione() -> Iterator:
    """Una connessione in transazione, con le impostazioni che l'esattezza chiede."""
    import psycopg

    with psycopg.connect(url()) as conn:
        with conn.cursor() as cur:
            # UTC: un timestamptz in uscita dipende dal fuso della sessione.
            cur.execute("SET TimeZone = 'UTC'")
            cur.execute("SET extra_float_digits = 1")
        yield conn


def errori_db() -> tuple[type[BaseException], ...]:
    """Gli errori che vengono dal database, non dal codice. Per chi li cattura."""
    import psycopg

    return (psycopg.Error, OSError)


def prefisso() -> str:
    valore = os.environ.get(VARIABILE_PREFISSO, "")
    if not re.fullmatch(r"[a-z_]*", valore):
        raise ValueError(f"{VARIABILE_PREFISSO}={valore!r}: solo minuscole e _")
    return valore


def _meta() -> str:
    """Lo schema del catalogo, gia' fra virgolette."""
    return _q(prefisso() + SCHEMA_META)


def prepara(cur) -> None:
    """
    Schemi e catalogo, idempotente.

    PERCHE' QUI E NON IN ALEMBIC. Le migrazioni di `backend/auth/migrations`
    descrivono tabelle con colonne fisse; queste hanno le colonne del
    DataFrame, e le crea la scrittura. Resta fisso solo il catalogo, che e'
    una tabella di cinque colonne: `goalmodel` non puo' importare `backend`
    (la dipendenza va nell'altro verso), e un secondo ambiente Alembic per una
    tabella sola costerebbe piu' di queste righe.
    """
    for s in (*LIVELLI, SCHEMA_META):
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_q(prefisso() + s)}")
    cur.execute(f"CREATE SEQUENCE IF NOT EXISTS {_meta()}.versioni")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {_meta()}.catalogo (
            livello       text        NOT NULL,
            nome          text        NOT NULL,
            tabella       text        NOT NULL,
            schema_arrow  bytea       NOT NULL,
            righe         bigint      NOT NULL,
            colonne       integer     NOT NULL,
            versione      bigint      NOT NULL,
            scritto_il    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (livello, nome)
        )""")


# ---------------------------------------------------------------------------
# Nomi
# ---------------------------------------------------------------------------

def _q(identificatore: str) -> str:
    """
    Identificatore fra virgolette: le colonne sono `B365>2.5`, `FTHG`.

    E' la regola di quoting di Postgres — virgolette doppie, quelle interne
    raddoppiate — la stessa di `psycopg.sql.Identifier`. I nomi di colonna
    vengono dalle intestazioni dei CSV scaricati (regola di sicurezza 3: dato
    ostile), quindi nessun identificatore entra in un SQL senza passare di qui.
    """
    return '"' + identificatore.replace('"', '""') + '"'


def tabella(livello: str, nome: str) -> tuple[str, str]:
    """
    `("raw", "whoscored/schedule_1516")` -> `("raw", "whoscored__schedule_1516")`.

    La barra e' una sottocartella di `data/`; in Postgres diventa `__`, che
    nessun nome di file del progetto contiene.
    """
    if livello not in LIVELLI:
        raise ValueError(f"livello sconosciuto: {livello}. Noti: {LIVELLI}")
    if not NOME_VALIDO.match(nome):
        raise ValueError(f"nome non ammesso per una tabella: {nome!r}")
    t = nome.replace("/", "__")
    if len((t + SUFFISSO_NUOVA).encode()) > MAX_IDENTIFICATORE:
        raise ValueError(f"nome troppo lungo per Postgres: {nome!r}")
    return prefisso() + livello, t


# ---------------------------------------------------------------------------
# Tipi
# ---------------------------------------------------------------------------

def tipo_pg(t: pa.DataType) -> str:
    """
    Il tipo Postgres che riporta indietro ESATTAMENTE quel tipo arrow.

    Un tipo che non c'e' qui solleva invece di finire in `text`: un ripiego
    silenzioso e' il modo in cui un intero diventa stringa e il merge sulla
    quadrupla smette di agganciare, cioe' il primo dei tre modi di rompersi
    scritti in `schema.py`.
    """
    if pa.types.is_floating(t):
        return "double precision"
    if pa.types.is_integer(t):
        if t == pa.uint64():
            raise TipoNonSupportato("uint64 non entra in bigint senza perdite")
        return "bigint"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_null(t):
        return "text"
    if pa.types.is_timestamp(t):
        return "timestamptz" if t.tz is not None else "timestamp"
    if pa.types.is_date32(t):
        return "date"
    if _annidato(t):
        return "jsonb"
    raise TipoNonSupportato(f"tipo arrow non supportato: {t}")


def _annidato(t: pa.DataType) -> bool:
    return (pa.types.is_list(t) or pa.types.is_large_list(t)
            or pa.types.is_struct(t))


def _in_lettura(t: pa.DataType) -> pa.DataType:
    """Il tipo che il CSV in uscita sa leggere; il cast all'originale viene dopo."""
    if pa.types.is_timestamp(t):
        return pa.timestamp("us")
    if (pa.types.is_null(t) or _annidato(t) or pa.types.is_large_string(t)):
        return pa.string()
    return t


# ---------------------------------------------------------------------------
# Arrow <-> CSV
# ---------------------------------------------------------------------------

def _a_csv(t: pa.Table) -> bytes:
    """
    La tabella come CSV per `COPY ... FROM STDIN (FORMAT csv)`.

    Due colonne vanno preparate: le annidate (le `incidents` di WhoScored)
    diventano JSON, che `jsonb` accetta e arrow ricostruisce; le `null`
    diventano stringhe nulle, perche' il writer CSV non sa scrivere il tipo
    null. I timestamp in nanosecondi passano solo se cadono sul microsecondo:
    Postgres non ha i nanosecondi, e arrotondare sarebbe cambiare il dato.
    """
    colonne = []
    for campo, col in zip(t.schema, t.columns, strict=True):
        tipo_pg(campo.type)  # solleva prima di scrivere, non a meta'
        if _annidato(campo.type):
            col = pa.array([None if v is None else json.dumps(v, ensure_ascii=False)
                            for v in col.to_pylist()], pa.string())
        elif pa.types.is_null(campo.type):
            col = pa.nulls(len(col), pa.string())
        elif pa.types.is_timestamp(campo.type) and campo.type.unit == "ns":
            import pyarrow.compute as pc

            interi = pc.fill_null(col.cast(pa.int64()), 0).to_numpy()
            if (interi % 1000).any():
                raise TipoNonSupportato(
                    f"'{campo.name}' ha timestamp con nanosecondi: Postgres "
                    f"arriva al microsecondo e li arrotonderebbe")
        colonne.append(col)
    scrivibile = pa.Table.from_arrays(colonne, names=t.schema.names)
    buf = io.BytesIO()
    pcsv.write_csv(scrivibile, buf, pcsv.WriteOptions(include_header=False))
    return buf.getvalue()


def _da_csv(dati: bytes, schema_arrow: pa.Schema) -> pa.Table:
    """Il CSV di `COPY ... TO STDOUT`, ricondotto allo schema salvato."""
    if not dati:
        return schema_arrow.empty_table()
    letta = pcsv.read_csv(
        io.BytesIO(dati),
        read_options=pcsv.ReadOptions(column_names=schema_arrow.names),
        convert_options=pcsv.ConvertOptions(
            column_types={f.name: _in_lettura(f.type) for f in schema_arrow},
            null_values=[""],
            strings_can_be_null=True,
            # `""` e' la stringa vuota, il campo vuoto e' NULL: e' cosi' che
            # Postgres li distingue in CSV, e cosi' restano distinti qui.
            quoted_strings_can_be_null=False,
            true_values=["t"],
            false_values=["f"],
        ),
    )
    colonne = []
    for campo in schema_arrow:
        col = letta.column(campo.name)
        if pa.types.is_null(campo.type):
            col = pa.nulls(len(col))
        elif _annidato(campo.type):
            col = pa.array([None if v is None else json.loads(v) for v in col.to_pylist()],
                           campo.type)
        else:
            # timestamp[us] -> timestamp[us, tz=UTC]: in arrow il cast verso un
            # fuso interpreta il valore come istante UTC, ed e' esattamente
            # cio' che `AT TIME ZONE 'UTC'` ha prodotto.
            col = col.cast(campo.type)
        colonne.append(col)
    return pa.Table.from_arrays(colonne, schema=schema_arrow)


# ---------------------------------------------------------------------------
# Vincoli: il contratto di `schema.py`, imposto dal database
# ---------------------------------------------------------------------------

def _vincoli(cur, nome_pg: str, livello: str, nome: str, campi: set[str]) -> None:
    """
    I tre modi di rompersi in silenzio, impossibili a livello di storage.

    Il contratto e' quello di `schema.PER_NOME`, non uno nuovo: la chiave non
    nulla, l'unicita' della quadrupla, e l'eccezione dello spareggio
    (Spezia-Verona 2022/23) come indice parziale su `week IS NOT NULL`. Una
    scrittura che li viola fallisce DENTRO la transazione, e la tabella
    precedente resta com'era.
    """
    t = f"{_q(livello)}.{_q(nome_pg)}"
    contratto = schema.PER_NOME.get(nome)
    if contratto is not None and set(contratto.chiave) <= campi:
        chiave = ", ".join(_q(c) for c in contratto.chiave)
        for c in contratto.chiave:
            cur.execute(f"ALTER TABLE {t} ALTER COLUMN {_q(c)} SET NOT NULL")
        if contratto.chiave_unica:
            cur.execute(f"CREATE UNIQUE INDEX {_q(nome_pg + '_chiave')} ON {t} ({chiave})")
        elif contratto.duplicati_ammessi_se_nullo in campi:
            marcatore = _q(contratto.duplicati_ammessi_se_nullo)
            cur.execute(f"CREATE UNIQUE INDEX {_q(nome_pg + '_chiave')} ON {t} ({chiave}) "
                        f"WHERE {marcatore} IS NOT NULL")
    # Le letture dell'API filtrano per lega e stagione.
    if {"league", "season"} <= campi:
        cur.execute(f"CREATE INDEX {_q(nome_pg + '_lega_stagione')} ON {t} "
                    f"({_q('league')}, {_q('season')})")


# ---------------------------------------------------------------------------
# Le quattro operazioni
# ---------------------------------------------------------------------------

def scrivi(livello: str, nome: str, df: pd.DataFrame) -> int:
    """
    Sostituisce la tabella con `df`, in UNA transazione. Restituisce la versione.

    ATOMICA, a differenza del parquet. Si scrive in una tabella nuova, si
    impongono i vincoli, poi si scambia con la vecchia: chi legge nel frattempo
    vede la versione precedente intera, mai una meta'. Se un vincolo salta,
    non cambia niente — il parquet invece si sovrascriveva comunque e il
    contratto se ne accorgeva solo alla lettura dopo.

    `preserve_index=False` e' cio' che `to_parquet(index=False)` passa ad
    arrow: stessa tabella di partenza, quindi stesso schema da salvare.
    """
    s, t = tabella(livello, nome)
    tab = pa.Table.from_pandas(df, preserve_index=False)
    csv = _a_csv(tab)
    nuova = t + SUFFISSO_NUOVA
    colonne = ", ".join(f"{_q(f.name)} {tipo_pg(f.type)}" for f in tab.schema)
    elenco = ", ".join(_q(f.name) for f in tab.schema)

    with connessione() as conn, conn.cursor() as cur:
        prepara(cur)
        # Due scritture dello stesso dato si mettono in fila, invece di
        # scambiarsi la tabella sotto i piedi a vicenda.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{livello}.{nome}",))
        cur.execute(f"DROP TABLE IF EXISTS {_q(s)}.{_q(nuova)}")
        cur.execute(f"CREATE TABLE {_q(s)}.{_q(nuova)} ({colonne})")
        with cur.copy(f"COPY {_q(s)}.{_q(nuova)} ({elenco}) "
                      f"FROM STDIN (FORMAT csv, NULL '')") as cp:
            cp.write(csv)
        cur.execute(f"DROP TABLE IF EXISTS {_q(s)}.{_q(t)}")
        cur.execute(f"ALTER TABLE {_q(s)}.{_q(nuova)} RENAME TO {_q(t)}")
        # I vincoli DOPO il cambio di nome: gli indici prendono il nome della
        # tabella, e quelli della vecchia sono spariti con lei.
        _vincoli(cur, t, s, nome, set(tab.schema.names))
        cur.execute(f"""
            INSERT INTO {_meta()}.catalogo
                (livello, nome, tabella, schema_arrow, righe, colonne, versione)
            VALUES (%s, %s, %s, %s, %s, %s, nextval('{_meta()}.versioni'))
            ON CONFLICT (livello, nome) DO UPDATE SET
                tabella = EXCLUDED.tabella, schema_arrow = EXCLUDED.schema_arrow,
                righe = EXCLUDED.righe, colonne = EXCLUDED.colonne,
                versione = EXCLUDED.versione, scritto_il = now()
            RETURNING versione""",
            (livello, nome, t, tab.schema.serialize().to_pybytes(),
             tab.num_rows, tab.num_columns))
        versione = cur.fetchone()[0]
        conn.commit()
    log.info("postgres %s.%s: %d righe, %d colonne (versione %d)",
             s, t, tab.num_rows, tab.num_columns, versione)
    return versione


def leggi(livello: str, nome: str, columns: list[str] | None = None) -> pd.DataFrame:
    """La tabella come la restituirebbe `read_parquet`, colonne comprese."""
    s, t = tabella(livello, nome)
    with connessione() as conn, conn.cursor() as cur:
        riga = _catalogo(cur, livello, nome)
        if riga is None:
            raise DatoAssente(f"{livello}/{nome} non c'e' in Postgres")
        schema_arrow = pa.ipc.read_schema(pa.py_buffer(riga[0]))
        if columns is not None:
            ignote = [c for c in columns if c not in schema_arrow.names]
            if ignote:
                # `read_parquet(columns=)` solleva allo stesso modo.
                raise KeyError(f"{livello}/{nome}: colonne inesistenti {ignote}")
            campi = [schema_arrow.field(c) for c in columns]
            schema_arrow = pa.schema(campi, metadata=schema_arrow.metadata)
        selezione = ", ".join(
            f"({_q(f.name)} AT TIME ZONE 'UTC')"
            if pa.types.is_timestamp(f.type) and f.type.tz is not None
            else _q(f.name)
            for f in schema_arrow
        ) or "NULL"
        buf = io.BytesIO()
        with cur.copy(f"COPY (SELECT {selezione} FROM {_q(s)}.{_q(t)}) "
                      f"TO STDOUT (FORMAT csv, NULL '')") as cp:
            for blocco in cp:
                buf.write(blocco)
    tab = _da_csv(buf.getvalue(), schema_arrow)
    return tab.to_pandas()


def _catalogo(cur, livello: str, nome: str):
    cur.execute(
        f"SELECT schema_arrow, versione FROM {_meta()}.catalogo "
        "WHERE livello = %s AND nome = %s", (livello, nome))
    return cur.fetchone()


def _catalogo_esiste(cur) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{_meta()}.catalogo",))
    return bool(cur.fetchone()[0])


def versione(livello: str, nome: str) -> int | None:
    """La versione corrente, per le cache. `None` se il dato non c'e'."""
    with connessione() as conn, conn.cursor() as cur:
        if not _catalogo_esiste(cur):
            return None
        riga = _catalogo(cur, livello, nome)
    return None if riga is None else int(riga[1])


def nomi(livello: str, pattern: str = "*") -> list[str]:
    """I nomi del livello che combaciano con `pattern` (glob, come i file)."""
    with connessione() as conn, conn.cursor() as cur:
        if not _catalogo_esiste(cur):
            return []
        cur.execute(f"SELECT nome FROM {_meta()}.catalogo WHERE livello = %s", (livello,))
        tutti = [r[0] for r in cur.fetchall()]
    return sorted(n for n in tutti if fnmatch.fnmatchcase(n, pattern))


def stato() -> pd.DataFrame:
    """Il catalogo, per `goalmodel db --stato`."""
    with connessione() as conn, conn.cursor() as cur:
        if not _catalogo_esiste(cur):
            return pd.DataFrame(columns=["livello", "nome", "righe", "colonne",
                                         "versione", "scritto_il"])
        cur.execute("SELECT livello, nome, righe, colonne, versione, scritto_il "
                    f"FROM {_meta()}.catalogo ORDER BY livello, nome")
        righe = cur.fetchall()
    return pd.DataFrame(righe, columns=["livello", "nome", "righe", "colonne",
                                        "versione", "scritto_il"])
