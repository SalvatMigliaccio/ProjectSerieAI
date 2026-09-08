"""
Blocco A: contesto della partita ricavato dal solo calendario.

COSA C'E' DENTRO
  riposo        giorni dall'ultima partita di quella squadra, COPPE INCLUSE
  congestione   partite nei 14 giorni precedenti, finestra (d-14, d)
  coppa         quante di quelle erano di coppa europea
  infrasettimanale  la partita cade fra martedi' e giovedi'
  derby         da `manual/derbies.csv`, coppia NON ordinata

Sono tutte date, non risultati: calcolabili prima del calcio d'inizio.

LE COPPE SONO IL MECCANISMO, NON UN CONTORNO
La prima versione di questo modulo le lasciava fuori, perche' `fbref_schedule`
contiene la sola Serie A e una partita di Champions del martedi' non compare
da nessuna parte. Quella versione e' stata misurata e scartata
(+0.00008, IC [-0.00007, +0.00024]) — ma misurava riposo e congestione **di
solo campionato**, che sono quasi uguali per tutti: le date delle giornate non
cambiano quando una squadra gioca in Europa. Era il blocco senza il suo
meccanismo, e quel risultato non chiudeva la domanda.

Il calendario UEFA arriva ora da `python ingest.py --stage cups` (Champions,
Europa e Conference League da FBref: solo calendario, una richiesta per
competizione e stagione, niente browser). Se il file manca il modulo funziona
lo stesso, contando le sole partite di campionato, e lo dichiara.

Le due scorciatoie che erano state scartate restano scartate:
  - dedurre chi gioca in Europa dalla classifica dell'anno prima e' una
    funzione dei risultati passati, cioe' esattamente cio' che il piano
    esclude: porta collinearita' con Elo, Dixon-Coles e medie mobili;
  - usare l'orario di calcio d'inizio come indizio e' un proxy debole di cui
    non si conosce l'affidabilita'.

CHE COSA E' UN DERBY QUI
La coppia e' NON ordinata: `tuple(sorted([casa, trasferta]))`. Inter-Milan e
Milan-Inter sono lo stesso derby, e trattarli come due voci diverse ne
perderebbe meta'. `manual/derbies.csv` e' facoltativo: se manca, le due
colonne del derby non vengono prodotte e si dice quale informazione manca —
non si finge che nessuna partita sia un derby, che sarebbe un dato falso
invece che assente.

LEAKAGE
Il riposo e la congestione guardano solo il passato: le partite con data
strettamente precedente. La partita corrente non entra mai nel proprio
conteggio. Un assert lo verifica.

Uso:
    python -m src.features.context
    python -m src.features.context --no-save
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from .. import config

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("context")

KEYS = ["league", "season", "home_team", "away_team"]

# Finestra della congestione, in giorni. Due settimane: e' l'orizzonte su cui
# si accumula il debito di recupero, e coincide con il ciclo di chi gioca
# infrasettimanale. Una finestra piu' lunga misurerebbe il calendario della
# stagione, non l'affaticamento.
FINESTRA_CONGESTIONE = 14

# Oltre questo, il "riposo" non e' riposo: e' la pausa estiva o una promozione
# dopo anni di Serie B. Si tronca perche' il differenziale casa-trasferta abbia
# un significato — senza, vale anche 1471 giorni.
RIPOSO_MASSIMO = 30.0

# Giorni della settimana considerati infrasettimanali (lunedi' = 0).
INFRASETTIMANALI = {1, 2, 3}

INTENSITA = {"city": 3, "regional": 2, "rivalry": 1}

# Le colonne prodotte. Elencate perche' `evaluate.load_dataset` e i modelli le
# devono poter distinguere dal resto senza indovinare da un prefisso.
FEATURES_CONTEXT = [
    "home_rest_days", "away_rest_days", "diff_rest_days",
    "home_matches_14d", "away_matches_14d", "diff_matches_14d",
    "home_cup_14d", "away_cup_14d", "diff_cup_14d",
    "is_midweek",
]
FEATURES_DERBY = ["is_derby", "derby_intensity"]


# ---------------------------------------------------------------------------
# Riposo e congestione
# ---------------------------------------------------------------------------

def carica_coppe() -> pd.DataFrame:
    """
    Le partite di coppa europea, in formato lungo (squadra, data).

    SONO IL MECCANISMO, NON UN CONTORNO. Il calendario di campionato non
    cambia quando una squadra gioca il martedi' in Champions: senza queste
    righe, riposo e congestione misurano il calendario di Serie A — che e'
    uguale per tutti — invece dell'affaticamento, che e' la cosa che
    dovrebbe contare. La prima versione del blocco A e' stata misurata senza,
    ed e' per questo che quel risultato non chiudeva la domanda.

    I nomi squadra passano per la stessa mappa del resto del progetto: FBref
    scrive `Internazionale` dove qui si scrive `Inter`. Le squadre straniere
    restano nel frame ma non agganciano nessuna riga di campionato, quindi non
    fanno danno.

    Se il file non c'e', si restituisce un frame vuoto e il blocco si calcola
    senza coppe — dichiarandolo, perche' e' esattamente la meta' che mancava.
    """
    path = config.RAW / "fbref_cups_schedule.parquet"
    if not path.exists():
        log.warning("%s assente: riposo e congestione contano le sole partite "
                    "di campionato. Lancia 'python ingest.py --stage cups'.",
                    path.name)
        return pd.DataFrame(columns=["team", "date"])

    from ..normalize import apply_name_map, load_name_map

    df = pd.read_parquet(path)
    df = apply_name_map(df, load_name_map())
    df = df.dropna(subset=["date"])
    righe = []
    for col in ("home_team", "away_team"):
        p = pd.DataFrame({"team": df[col], "date": pd.to_datetime(df["date"])})
        righe.append(p)
    out = pd.concat(righe, ignore_index=True).dropna().drop_duplicates()
    log.info("coppe: %d presenze di squadra, dal %s al %s",
             len(out), out["date"].min().date(), out["date"].max().date())
    return out


def to_long(df: pd.DataFrame, coppe: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Una riga per squadra per partita: il riposo e' della squadra, non della
    partita.

    Le partite di coppa entrano come righe in piu', con `_riga` nullo: non
    producono output — non sono partite da predire — ma contano nel riposo e
    nella congestione di quelle che seguono, che e' tutto il punto.
    """
    parti = []
    for lato, col in (("home", "home_team"), ("away", "away_team")):
        p = df[KEYS + ["date"]].copy()
        p["team"] = df[col]
        p["venue"] = lato
        p["_riga"] = df.index
        p["is_cup"] = False
        parti.append(p)

    if coppe is not None and not coppe.empty:
        c = coppe.copy()
        c["venue"] = "cup"
        c["_riga"] = pd.NA
        c["is_cup"] = True
        parti.append(c)

    long = pd.concat(parti, ignore_index=True)
    long["date"] = pd.to_datetime(long["date"])
    return long.sort_values(["team", "date"]).reset_index(drop=True)


def riposo_e_congestione(long: pd.DataFrame) -> pd.DataFrame:
    """
    Giorni dall'ultima partita e partite nei 14 giorni precedenti, per squadra.

    Entrambi guardano SOLO le partite con data strettamente precedente. Il
    confronto stretto non e' pignoleria: due partite lo stesso giorno non
    esistono in un campionato, ma un rinvio mal registrato le creerebbe, e
    contare la partita corrente nel proprio storico e' leakage.

    Il conteggio usa `searchsorted` sulle date gia' ordinate della squadra:
    con 9000 righe un doppio ciclo costerebbe secondi, questo costa
    millisecondi e non sbaglia i confini.
    """
    out = long.copy()
    out["rest_days"] = np.nan
    out["matches_14d"] = 0
    out["cup_14d"] = 0

    for _, idx in out.groupby("team", sort=False).groups.items():
        date = out.loc[idx, "date"].to_numpy("datetime64[ns]")
        # Ordine garantito dal sort in to_long: qui si verifica, perche' se
        # saltasse searchsorted darebbe numeri plausibili e sbagliati.
        assert (np.diff(date) >= np.timedelta64(0, "D")).all(), "date non ordinate"

        precedente = np.empty(len(date), dtype="datetime64[ns]")
        precedente[0] = np.datetime64("NaT", "ns")
        precedente[1:] = date[:-1]
        giorni = (date - precedente) / np.timedelta64(1, "D")
        # Oltre un mese non e' piu' riposo: e' la pausa fra due stagioni, o una
        # squadra che torna dalla B dopo anni (il massimo grezzo e' 1548
        # giorni). Troncare conta soprattutto per il DIFFERENZIALE, che
        # altrimenti vale 1471 e non significa niente. Per gli alberi il
        # troncamento sui livelli e' indifferente — si dividono sull'ordine,
        # non sulla scala — ma la differenza di due code non e' monotona.
        out.loc[idx, "rest_days"] = np.minimum(giorni, RIPOSO_MASSIMO)

        # Finestra APERTA DA ENTRAMBI I LATI: (d - 14, d).
        #   a destra  esclude la partita corrente e le sue eventuali gemelle,
        #             ed e' la meta' che impedisce il leakage;
        #   a sinistra esclude quella di esattamente 14 giorni prima, che a due
        #             settimane di distanza non affatica piu' nessuno. Il
        #             confine conta: con 'left' una partita in piu' entra in
        #             ogni conteggio dei turni regolari, e nessun sintomo lo
        #             segnala.
        inizio = date - np.timedelta64(FINESTRA_CONGESTIONE, "D")
        sotto = np.searchsorted(date, inizio, side="right")
        corrente = np.searchsorted(date, date, side="left")
        out.loc[idx, "matches_14d"] = corrente - sotto

        # Quante di quelle erano di coppa. E' il conteggio che porta il
        # meccanismo: due partite in 14 giorni di solo campionato sono la
        # norma, due di cui una in Champions il martedi' no.
        coppa = out.loc[idx, "is_cup"].to_numpy().astype(int)
        cumulate = np.concatenate([[0], np.cumsum(coppa)])
        out.loc[idx, "cup_14d"] = cumulate[corrente] - cumulate[sotto]

    return out


def to_wide(df: pd.DataFrame, long: pd.DataFrame) -> pd.DataFrame:
    """Riporta riposo e congestione sulla riga della partita, casa e trasferta."""
    out = df[KEYS + ["date"]].copy()
    out["date"] = pd.to_datetime(out["date"])

    for lato in ("home", "away"):
        p = long[long["venue"] == lato].set_index("_riga")
        out[f"{lato}_rest_days"] = p["rest_days"].reindex(df.index).to_numpy()
        out[f"{lato}_matches_14d"] = p["matches_14d"].reindex(df.index).to_numpy()
        out[f"{lato}_cup_14d"] = p["cup_14d"].reindex(df.index).to_numpy()

    # Il differenziale e' spesso piu' informativo dei livelli: una partita e'
    # un confronto fra due condizioni, non due misure indipendenti.
    out["diff_rest_days"] = out["home_rest_days"] - out["away_rest_days"]
    out["diff_matches_14d"] = out["home_matches_14d"] - out["away_matches_14d"]
    out["diff_cup_14d"] = out["home_cup_14d"] - out["away_cup_14d"]
    out["is_midweek"] = out["date"].dt.dayofweek.isin(INFRASETTIMANALI).astype(int)
    return out


# ---------------------------------------------------------------------------
# Derby
# ---------------------------------------------------------------------------

def carica_derby() -> pd.DataFrame | None:
    """
    `manual/derbies.csv`, con la coppia normalizzata in ordine alfabetico.

    Restituisce None se il file non c'e': il chiamante deve poter distinguere
    "nessun derby in questa giornata" da "non so quali siano i derby".
    """
    path = config.DERBIES
    if not path.exists():
        log.warning("%s assente: le colonne del derby non verranno prodotte. "
                    "Colonne attese: home_team, away_team, intensity "
                    "(city/regional/rivalry).", path)
        return None

    d = pd.read_csv(path)
    mancanti = {"home_team", "away_team", "intensity"} - set(d.columns)
    if mancanti:
        log.warning("%s malformato, colonne mancanti %s: derby ignorato",
                    path.name, sorted(mancanti))
        return None

    ignote = set(d["intensity"]) - set(INTENSITA)
    if ignote:
        log.warning("intensita' non riconosciute in %s: %s (trattate come 'rivalry')",
                    path.name, sorted(ignote))

    coppie = [tuple(sorted((a, b))) for a, b in zip(d["home_team"], d["away_team"])]
    d = d.assign(coppia=coppie).drop_duplicates(subset="coppia")
    log.info("derby caricati: %d coppie (%s)", len(d),
             ", ".join(f"{k} {v}" for k, v in d["intensity"].value_counts().items()))
    return d


def aggiungi_derby(out: pd.DataFrame, derby: pd.DataFrame | None) -> pd.DataFrame:
    """Due colonne sole: se e' un derby, e quanto sentito."""
    if derby is None:
        return out
    mappa = dict(zip(derby["coppia"], derby["intensity"]))
    coppie = [tuple(sorted((a, b)))
              for a, b in zip(out["home_team"], out["away_team"])]
    intensita = [mappa.get(c) for c in coppie]

    out["is_derby"] = [int(i is not None) for i in intensita]
    out["derby_intensity"] = [INTENSITA.get(i, 1) if i is not None else 0
                              for i in intensita]

    n = int(out["is_derby"].sum())
    log.info("derby riconosciuti: %d partite su %d (%.1f%%)",
             n, len(out), 100 * n / max(len(out), 1))
    if n == 0:
        log.warning("nessun derby agganciato: i nomi in derbies.csv non "
                    "corrispondono a quelli del dataset?")
    return out


# ---------------------------------------------------------------------------

def assert_no_leakage(out: pd.DataFrame, long: pd.DataFrame) -> None:
    """
    Il riposo dev'essere positivo e la congestione non puo' contare la
    partita stessa.

    Un riposo di zero giorni significherebbe che la stessa squadra compare due
    volte nella stessa data: o un rinvio registrato male, o la partita
    corrente entrata nel proprio storico.
    """
    r = long["rest_days"].dropna()
    assert (r > 0).all(), (
        f"{int((r <= 0).sum())} righe con riposo <= 0 giorni: la partita "
        f"corrente e' entrata nel proprio storico, oppure due partite della "
        f"stessa squadra hanno la stessa data."
    )
    massimo = long.groupby("team")["matches_14d"].max().max()
    assert massimo <= FINESTRA_CONGESTIONE, (
        f"{massimo} partite in {FINESTRA_CONGESTIONE} giorni: impossibile"
    )


def build(df: pd.DataFrame | None = None, save: bool = True) -> pd.DataFrame:
    """
    `save=False` serve a src/predict.py: li' il frame contiene anche le partite
    non ancora giocate, e il risultato non deve sovrascrivere il parquet delle
    feature storiche.

    Le righe future non sono un caso speciale: riposo e congestione guardano
    indietro, e per una partita da giocare guardano indietro esattamente come
    per una gia' giocata.
    """
    if df is None:
        path = config.INTERIM / "matches_master.parquet"
        df = pd.read_parquet(path)
        log.info("caricato %s: %d righe", path.name, len(df))

    df = df.reset_index(drop=True)
    long = riposo_e_congestione(to_long(df, carica_coppe()))
    out = to_wide(df, long)
    assert_no_leakage(out, long)
    out = aggiungi_derby(out, carica_derby())

    prodotte = [c for c in FEATURES_CONTEXT + FEATURES_DERBY if c in out.columns]
    log.info("feature di contesto: %s", prodotte)
    log.info("riposo mediano %.1f giorni, congestione mediana %.1f partite/14g",
             out["home_rest_days"].median(), out["home_matches_14d"].median())
    log.info("partite infrasettimanali: %d su %d (%.1f%%)",
             int(out["is_midweek"].sum()), len(out),
             100 * out["is_midweek"].mean())

    if save:
        dst = config.PROCESSED / "features_context.parquet"
        out.to_parquet(dst, index=False)
        log.info("scritto %s: %d righe, %d colonne", dst.name, len(out), out.shape[1])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Blocco A: contesto ricavato dal calendario"
    )
    ap.add_argument("--no-save", action="store_true",
                    help="calcola senza scrivere features_context.parquet")
    args = ap.parse_args()

    pd.set_option("display.width", 200)
    out = build(save=not args.no_save)

    print("\n=== DISTRIBUZIONE DELLE FEATURE DI CONTESTO ===")
    colonne = [c for c in FEATURES_CONTEXT + FEATURES_DERBY if c in out.columns]
    print(out[colonne].describe().T.round(2).to_string())

    if "is_derby" in out.columns:
        print("\n=== DERBY PER INTENSITA' ===")
        etichette = {0: "nessuno", 1: "rivalry", 2: "regional", 3: "city"}
        conteggi = out["derby_intensity"].map(etichette).value_counts()
        print(conteggi.to_string())


if __name__ == "__main__":
    main()
