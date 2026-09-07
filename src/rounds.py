"""
La giornata di campionato come unita' di lavoro, con il suo ciclo di vita.

PERCHE' LA GIORNATA E NON IL GIORNO DELLA SETTIMANA
La versione precedente di questo codice si lanciava "il sabato mattina" e
faceva tutto in fila. Funziona finche' il calendario e' regolare, e il
calendario non lo e' mai: un turno infrasettimanale, un rinvio per neve, una
partita spostata alla settimana dopo per la coppa, e "sabato mattina" non
individua piu' niente. La giornata invece e' un oggetto con uno stato, e lo
stato si deduce dai dati:

    futura     nessuna quota disponibile
    aperta     quote presenti, partite non ancora giocate
    predetta   previsioni registrate (tutte, o solo una parte)
    giocata    tutti i risultati disponibili
    chiusa     risultati agganciati, errore calcolato, giornata archiviata

I due comandi che fanno avanzare una giornata lungo questo ciclo sono
`src/predict_round.py` (da aperta a predetta) e `src/close_round.py` (da
giocata a chiusa). Nessuno dei due contiene un giorno della settimana, e
nessuno dei due chiede quale giornata: la deducono da qui.

LA TRANSIZIONE E' UN FILE, NON UNA COLONNA
Una giornata e' chiusa se e solo se esiste
`track_record/rounds/round_<stagione>_<NN>.csv`. Non c'e' uno stato scritto da
qualche parte che potrebbe divergere dai fatti: il file c'e' o non c'e'. Per
questo quei file sono versionati — sono la memoria di cosa e' gia' stato
archiviato, e in un clone senza di loro tutte le giornate tornerebbero aperte.

Uso:
    python -m src.rounds --status
    python -m src.rounds --status --season 2526
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import config
from . import predict as predict_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("rounds")

KEYS = ["league", "season", "home_team", "away_team"]
SEP = "=" * 72

# Le giornate archiviate. Versionate: l'esistenza del file E' lo stato.
ROUNDS = config.TRACK_RECORD / "rounds"
RIEPILOGO = ROUNDS / "riepilogo.csv"

FUTURA = "futura"
APERTA = "aperta"
PREDETTA = "predetta"
PARZIALE = "predetta in parte"
GIOCATA = "giocata"
CHIUSA = "chiusa"

# Ordine di avanzamento, per stampare e per ragionare. Non e' decorativo:
# `da_predire` e `da_chiudere` scorrono le giornate in ordine di calendario e
# si fermano alla prima che ha ancora qualcosa da fare.
ORDINE = [FUTURA, APERTA, PARZIALE, PREDETTA, GIOCATA, CHIUSA]


class PassoFallito(RuntimeError):
    """Un passo fatale non e' andato a buon fine: il comando si ferma qui."""


# ---------------------------------------------------------------------------
# Infrastruttura comune ai due comandi
# ---------------------------------------------------------------------------

def titolo(testo: str) -> None:
    print(f"\n{SEP}\n  {testo}\n{SEP}")


def passo(numero: str, nome: str, fn: Callable, fatale: bool) -> bool:
    """
    Esegue un passo, riferendo con chiarezza cosa e' successo.

    I passi di rete non sono fatali: football-data risponde 503 piu' spesso di
    quanto dovrebbe, e restare senza previsione perche' un sito era giu' dieci
    minuti sarebbe il modo peggiore di fallire. I passi locali si': su dati
    incoerenti qualsiasi previsione sarebbe sbagliata in silenzio.
    """
    log.info("--- [%s] %s", numero, nome)
    try:
        fn()
        return True
    except Exception as exc:
        if fatale:
            raise PassoFallito(
                f"passo [{numero}] '{nome}' fallito: {exc}\n"
                f"    Il comando si ferma qui: proseguire produrrebbe una "
                f"previsione su dati incoerenti."
            ) from exc
        log.warning("passo [%s] '%s' fallito: %s", numero, nome, exc)
        log.warning("non e' fatale: si prosegue con i dati gia' presenti")
        return False


def silenzia(verbose: bool) -> None:
    """
    Abbassa il rumore dei moduli chiamati.

    Un comando esegue sei moduli che parlano molto: il de-vigging da solo
    stampa otto righe di copertura per book. Sono utili quando si lancia quel
    modulo da solo, sono rumore quando si vuole leggere una tabella. Gli
    avvisi passano comunque: e' l'informazione che non si deve perdere.
    """
    if verbose:
        return
    for nome in ("normalize", "form", "market", "predict", "evaluate",
                 "ingest", "backtest_log", "baseline", "report", "gbm"):
        logging.getLogger(nome).setLevel(logging.WARNING)


def aggiorna_dati(stage: tuple[str, ...], skip_ingest: bool) -> list[str]:
    """Gli stage di rete richiesti. Restituisce l'elenco dei falliti."""
    if skip_ingest:
        log.info("--- [1] ingestion saltata su richiesta (--skip-ingest)")
        return []

    import ingest

    disponibili = {
        "matches": ("risultati football-data", ingest.ingest_matches),
        "understat": ("xG e PPDA Understat", ingest.ingest_understat),
        "schedule": ("calendario FBref", ingest.ingest_schedule),
        "fixtures": ("quote del turno imminente", ingest.ingest_fixtures),
    }
    falliti = []
    for i, nome_stage in enumerate(stage, start=1):
        etichetta, fn = disponibili[nome_stage]
        if not passo(f"1{chr(96 + i)}", etichetta, fn, fatale=False):
            falliti.append(etichetta)
    return falliti


def ricostruisci() -> None:
    """
    Dataset e feature, tutti fatali.

    `normalize.cmd_build` NON e' facoltativo nemmeno quando si vuole solo
    chiudere una giornata: `ingest --stage matches` aggiorna soltanto
    `data/raw/matches.parquet`, e finche' non si ricostruisce
    `matches_master.parquet` i risultati appena scaricati non esistono per il
    resto del progetto. Saltarlo farebbe cercare invano i risultati di partite
    gia' giocate.
    """
    from . import normalize
    from .features import form, market

    passo("2", "matches_master", normalize.cmd_build, fatale=True)
    passo("3a", "feature di forma", form.build, fatale=True)
    passo("3b", "feature di mercato", market.build, fatale=True)


# ---------------------------------------------------------------------------
# Lo stato delle giornate
# ---------------------------------------------------------------------------

def file_giornata(season: str, matchday: int) -> Path:
    return ROUNDS / f"round_{season}_{int(matchday):02d}.csv"


def calendario() -> pd.DataFrame:
    """Il calendario completo, normalizzato, con giornata e calcio d'inizio."""
    from .normalize import apply_name_map, load_name_map, load_raw, normalize_season

    sched = normalize_season(apply_name_map(load_raw("fbref_schedule"), load_name_map()))
    sched = sched[sched["league"].isin(config.LEAGUES)].dropna(subset=["week"]).copy()
    sched["matchday"] = sched["week"].astype(int)
    sched["kickoff"] = predict_mod.kickoff(sched)
    sched["date"] = pd.to_datetime(sched["date"])
    return sched[KEYS + ["matchday", "date", "kickoff"]].sort_values(
        ["kickoff", "home_team"]
    ).reset_index(drop=True)


def stagione_corrente(sched: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """
    La stagione su cui si lavora: quella della prima partita ancora da giocare.

    Non si legge da `config.CURRENT_SEASON`, che va aggiornato a mano ogni
    agosto e che quindi prima o poi sara' sbagliato. Se non c'e' piu' niente da
    giocare si prende l'ultima stagione presente in calendario, che a stagione
    finita e' la risposta giusta.
    """
    futuri = sched[sched["kickoff"] > as_of]
    if not futuri.empty:
        return str(futuri.loc[futuri["kickoff"].idxmin(), "season"])
    if sched.empty:
        return config.CURRENT_SEASON
    return str(sched["season"].max())


def _chiavi(df: pd.DataFrame) -> pd.Series:
    """La quadrupla come tupla di stringhe: la stessa che usa il registro."""
    return pd.Series(
        list(map(tuple, df[KEYS].astype(str).to_numpy())), index=df.index
    )


def stato_giornate(
    season: str | None = None,
    model_version: str | None = None,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Una riga per giornata, con tutto quello che serve a decidere cosa fare.

    Le quote si contano con la STESSA precedenza di fonti che usa la
    previsione (`predict.attach_odds`): snapshot scaricato, poi ripiego
    manuale, poi lo storico per le partite gia' giocate. Contarle in un altro
    modo produrrebbe una tabella che dice "aperta" mentre la previsione trova
    zero quote, ed e' esattamente il tipo di divergenza che fa cercare il
    problema nel posto sbagliato.

    Le previsioni si contano a parita' di `model_version`, perche' quella e' la
    chiave di deduplicazione del registro: una partita predetta da un altro
    modello e' ancora da predire per questo.
    """
    as_of = as_of or pd.Timestamp.now(tz="UTC")
    model_version = model_version or predict_mod.MarketOnly().name

    sched = calendario()
    season = season or stagione_corrente(sched, as_of)
    sched = sched[sched["season"].astype(str) == str(season)].copy()
    if sched.empty:
        log.warning("nessuna partita in calendario per la stagione %s", season)
        return pd.DataFrame()

    master = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    giocate = set(_chiavi(master[master["FTR"].notna()]))
    # Il percorso si passa esplicitamente invece di lasciare il default, che
    # `already_logged` fissa all'import: e' l'unico modo di far girare lo
    # stato su un registro finto in un test.
    predette = predict_mod.already_logged(
        model_version, path=predict_mod.PREDICTIONS_LOG)

    # Le quote passano dalla funzione della previsione, non da una copia.
    con_quote = predict_mod.attach_odds(sched[KEYS].copy(), master)
    sched["ha_quote"] = con_quote["odds_from"].notna().to_numpy()

    chiavi = _chiavi(sched)
    sched["giocata"] = chiavi.isin(giocate)
    sched["predetta"] = chiavi.isin(predette)
    sched["futura"] = sched["kickoff"] > as_of

    righe = []
    for matchday, g in sched.groupby("matchday", sort=True):
        chiusa = file_giornata(season, matchday).exists()
        n, n_gio = len(g), int(g["giocata"].sum())
        n_pre, n_quo = int(g["predetta"].sum()), int(g["ha_quote"].sum())
        # Predicibili: hanno le quote, non sono ancora in registro e il
        # fischio d'inizio deve ancora arrivare. E' l'unico conteggio che
        # dice davvero se c'e' lavoro da fare.
        n_pred_ora = int((g["ha_quote"] & ~g["predetta"] & g["futura"]).sum())

        if chiusa:
            stato = CHIUSA
        elif n_gio == n and n:
            stato = GIOCATA
        elif n_pre == n:
            stato = PREDETTA
        elif n_pre:
            stato = PARZIALE
        elif n_quo:
            stato = APERTA
        else:
            stato = FUTURA

        righe.append({
            "season": str(season), "matchday": int(matchday),
            "prima_data": g["date"].min(), "ultima_data": g["date"].max(),
            "n_partite": n, "n_quote": n_quo, "n_predette": n_pre,
            "n_giocate": n_gio, "n_predicibili": n_pred_ora,
            "stato": stato, "rps": _rps_giornata(season, matchday) if chiusa else np.nan,
        })
    return pd.DataFrame(righe).sort_values("matchday").reset_index(drop=True)


def _rps_giornata(season: str, matchday: int) -> float:
    """L'RPS archiviato di una giornata chiusa, riletto dal suo file."""
    try:
        d = pd.read_csv(file_giornata(season, matchday))
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return np.nan
    if "rps" not in d.columns or "valida" not in d.columns:
        return np.nan
    valide = d[d["valida"].astype(bool)]
    return float(valide["rps"].mean()) if not valide.empty else np.nan


def da_predire(tab: pd.DataFrame) -> pd.Series | None:
    """La prima giornata che ha ancora partite predicibili adesso."""
    if tab.empty:
        return None
    candidate = tab[(tab["stato"].isin([APERTA, PARZIALE, PREDETTA]))
                    & (tab["n_predicibili"] > 0)]
    return None if candidate.empty else candidate.iloc[0]


def da_chiudere(tab: pd.DataFrame) -> pd.Series | None:
    """
    La prima giornata predetta, interamente giocata e non ancora archiviata.

    "Interamente" non e' pignoleria: chiudere una giornata a cui manca il
    risultato di un posticipo significa archiviare un RPS calcolato su nove
    partite su dieci e non poterlo piu' correggere, perche' la giornata
    risulterebbe gia' chiusa al lancio successivo.
    """
    if tab.empty:
        return None
    candidate = tab[(tab["stato"] == GIOCATA) & (tab["n_predette"] > 0)]
    return None if candidate.empty else candidate.iloc[0]


def perche_niente_da_fare(tab: pd.DataFrame, azione: str) -> str:
    """
    Il messaggio esplicito quando non c'e' niente da fare.

    Uscire in silenzio con codice zero e' peggio che fallire: non si distingue
    "e' tutto a posto" da "il comando non ha trovato i dati". Qui si nomina la
    giornata e il suo stato.
    """
    if tab.empty:
        return "Nessuna giornata in calendario: serve un'ingestion aggiornata."

    if azione == "predire":
        # La prima giornata ancora predicibile in linea di principio: non
        # chiusa, non gia' giocata per intero, e con partite non in registro.
        resta = tab[(~tab["stato"].isin([CHIUSA, GIOCATA]))
                    & (tab["n_predette"] < tab["n_partite"])]
        if resta.empty:
            return ("Ogni giornata del calendario e' gia' predetta, giocata o "
                    "chiusa: non c'e' niente da predire.")
        g = resta.iloc[0]
        if g["stato"] == FUTURA:
            return (
                f"La prossima giornata da predire e' la {g['matchday']} "
                f"({g['prima_data']:%d/%m}), ma e' ancora FUTURA: nessuna quota "
                f"disponibile.\nLo snapshot di football-data copre solo il turno "
                f"imminente. Rilancia quando le quote saranno pubblicate."
            )
        # Ha le quote e non e' tutta in registro, eppure non c'e' niente da
        # fare: le partite che mancano hanno gia' iniziato. Non si recuperano
        # — una previsione scritta dopo il fischio non e' una previsione — e la
        # giornata restera' 'predetta in parte' per sempre. Va detto, non
        # nascosto dietro un "niente da fare".
        mancanti = int(g["n_partite"] - g["n_predette"])
        return (
            f"La giornata {g['matchday']} ha {g['n_predette']}/{g['n_partite']} "
            f"partite in registro, e le {mancanti} che mancano hanno gia' "
            f"iniziato.\nNon sono piu' predicibili: restera' predetta in parte. "
            f"La prossima occasione e' la giornata successiva, quando usciranno "
            f"le quote."
        )

    chiudibili = tab[(tab["stato"] != CHIUSA) & (tab["n_predette"] > 0)]
    if chiudibili.empty:
        return ("Nessuna giornata con previsioni in registro da chiudere: "
                "predici prima con 'python -m src.predict_round'.")
    g = chiudibili.iloc[0]
    return (
        f"La giornata {g['matchday']} e' {g['stato']}: mancano i risultati di "
        f"{int(g['n_partite'] - g['n_giocate'])} partite su {g['n_partite']}.\n"
        f"Non la chiudo: un RPS calcolato su una giornata incompleta finirebbe "
        f"in archivio e non sarebbe piu' correggibile."
    )


# ---------------------------------------------------------------------------
# Vista a terminale
# ---------------------------------------------------------------------------

def stampa_stato(tab: pd.DataFrame, season: str) -> None:
    if tab.empty:
        print("Nessuna giornata da mostrare.")
        return

    titolo(f"STATO DELLE GIORNATE  |  Serie A {season}")
    intest = (f"  {'gio':>3}  {'date':<13} {'part':>5} {'quote':>6} {'prev':>5} "
              f"{'gioc':>5}  {'stato':<18} {'RPS':>7}")
    print(intest)
    print("  " + "-" * (len(intest) - 2))

    for _, r in tab.iterrows():
        date = (f"{r['prima_data']:%d/%m}" if r["prima_data"] == r["ultima_data"]
                else f"{r['prima_data']:%d/%m}-{r['ultima_data']:%d/%m}")
        rps = f"{r['rps']:.4f}" if pd.notna(r["rps"]) else "-"
        # Una freccia sulle due giornate su cui i comandi agirebbero adesso:
        # e' l'informazione che si cerca guardando questa tabella.
        marca = " "
        if r["n_predicibili"] > 0 and r["stato"] in (APERTA, PARZIALE, PREDETTA):
            marca = ">"
        elif r["stato"] == GIOCATA and r["n_predette"] > 0:
            marca = "*"
        print(f"{marca} {r['matchday']:>3}  {date:<13} {r['n_partite']:>5} "
              f"{r['n_quote']:>6} {r['n_predette']:>5} {r['n_giocate']:>5}  "
              f"{r['stato']:<18} {rps:>7}")

    conteggi = tab["stato"].value_counts()
    riassunto = "  ".join(f"{s} {int(conteggi[s])}" for s in ORDINE if s in conteggi)
    print(f"\n  {riassunto}")
    print("  '>' = predicibile ora con 'python -m src.predict_round'")
    print("  '*' = chiudibile ora con 'python -m src.close_round'")

    chiuse = tab[tab["stato"] == CHIUSA].dropna(subset=["rps"])
    if not chiuse.empty:
        print(f"\n  RPS medio sulle {len(chiuse)} giornate chiuse: "
              f"{chiuse['rps'].mean():.4f}   "
              f"(backtest {config.TEST_RPS_REFERENCE:.4f})")
    print(SEP)


def riepilogo_due_righe(fatto: str, sospeso: str) -> None:
    """
    Le due righe che si leggono senza scorrere il log.

    Sono in fondo e sono sempre due: cosa e' stato fatto, cosa resta. Tutto il
    resto e' dettaglio consultabile, questo no.
    """
    print()
    print(f"  FATTO:      {fatto}")
    print(f"  IN SOSPESO: {sospeso}")
    print(SEP)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Stato delle giornate di campionato"
    )
    ap.add_argument("--status", action="store_true",
                    help="tabella dello stato di ogni giornata della stagione")
    ap.add_argument("--season", help="stagione da mostrare (default: quella in corso)")
    ap.add_argument("--verbose", action="store_true", help="log completi")
    args = ap.parse_args()

    silenzia(args.verbose)
    tab = stato_giornate(season=args.season)
    stagione = args.season or (tab["season"].iloc[0] if not tab.empty else "?")
    stampa_stato(tab, stagione)


if __name__ == "__main__":
    main()
