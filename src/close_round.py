"""
Fa avanzare una giornata da GIOCATA a CHIUSA.

COSA FA, NELL'ORDINE
  1. aggiorna i risultati (`matches`, `understat`) e ricostruisce il dataset;
  2. individua da solo la prima giornata predetta, interamente giocata e non
     ancora archiviata;
  3. aggancia i risultati veri alle previsioni registrate;
  4. salva la giornata in `track_record/rounds/round_<stagione>_<NN>.csv`, con
     previsione, risultato ed errore di ogni singola partita;
  5. rifa' il riepilogo cumulativo di produzione;
  6. rigenera il report con il track record aggiornato.

UNA GIORNATA INCOMPLETA NON SI CHIUDE
Se anche una sola partita non ha ancora il risultato — un posticipo, un rinvio
— la giornata resta aperta e si riprova al lancio successivo. Chiuderla
significherebbe archiviare un RPS calcolato su nove partite su dieci, e non
poterlo piu' correggere: al lancio dopo la giornata risulterebbe gia' chiusa e
nessuno tornerebbe a guardarla. Meglio aspettare tre settimane il recupero.

PERCHE' RICOSTRUISCE IL DATASET
`ingest --stage matches` aggiorna solo `data/raw/matches.parquet`. Finche' non
si rilancia `normalize --build`, i risultati appena scaricati non esistono per
il resto del progetto e l'aggancio non troverebbe niente. Non e' un passo
facoltativo, e' la meta' del lavoro.

IL TRACK RECORD NON TORNA INDIETRO SUL MODELLO
Riaddestrare sui risultati nuovi e' automatico e legittimo: il modello ha piu'
dati. Aggiustare il modello DOPO aver visto come e' andato no, ed e' il motivo
per cui questo comando non tocca niente che riguardi il modello: scrive
soltanto cosa e' successo.

Uso:
    python -m src.close_round
    python -m src.close_round --round 3
    python -m src.close_round --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd

from . import config
from . import predict as predict_mod
from . import report as report_mod
from . import rounds
from .report import quando

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("close_round")

KEYS = rounds.KEYS
PROB = ["p_home", "p_draw", "p_away"]
STAGE = ("matches", "understat")

# Le colonne del file di giornata. Ordine pensato per essere letto da un umano
# in un foglio di calcolo: prima cosa era stato previsto, poi cosa e'
# successo, poi quanto si e' sbagliato.
COLONNE = [
    "season", "matchday", "match_date", "home_team", "away_team",
    "model_version", "timestamp_prediction",
    "p_home", "p_draw", "p_away", "p_over25", "p_btts",
    "lambda_home", "lambda_away",
    "odds_home", "odds_draw", "odds_away", "odds_source",
    "FTHG", "FTAG", "FTR", "esito_previsto", "azzeccato",
    "rps", "log_loss", "brier", "rps_mercato", "valida",
]


def costruisci_giornata(season: str, matchday: int,
                        model_version: str | None = None) -> pd.DataFrame:
    """
    Previsioni della giornata + risultati veri + errore, una riga per partita.

    Le righe scritte DOPO il calcio d'inizio restano nel file ma con
    `valida=False`: non sono previsioni e non devono entrare in nessuna media.
    Toglierle del tutto sarebbe peggio — il registro conserva anche gli
    errori, e un file di giornata che non li mostra racconta una storia piu'
    pulita di quella vera.
    """
    from . import backtest_log as bl
    from .evaluate import brier, log_loss, outcome_index, rps

    preds = bl.load_log(path=predict_mod.PREDICTIONS_LOG)
    preds = preds[(preds["season"].astype(str) == str(season))
                  & (preds["matchday"].astype(int) == int(matchday))]
    if model_version:
        preds = preds[preds["model_version"] == model_version]
    if preds.empty:
        return pd.DataFrame(columns=COLONNE)

    d = bl.attach_results(preds).copy()
    d["valida"] = ~d["post_kickoff"] & d["FTR"].notna()

    p = d[PROB].to_numpy(float)
    # `outcome_index` non tollera i NaN: le partite senza risultato si
    # calcolano su un esito fittizio e poi si azzerano con la maschera.
    ftr = d["FTR"].fillna("H")
    y = outcome_index(ftr)
    d["rps"] = np.where(d["valida"], rps(p, y), np.nan)
    d["log_loss"] = np.where(d["valida"], log_loss(p, y), np.nan)
    d["brier"] = np.where(d["valida"], brier(p, y), np.nan)

    d["esito_previsto"] = np.array(["H", "D", "A"])[p.argmax(axis=1)]
    d["azzeccato"] = np.where(d["valida"], d["esito_previsto"] == d["FTR"], np.nan)

    # L'RPS delle quote registrate, riga per riga: e' il riferimento onesto,
    # perche' viene dalle quote di QUEL momento e non da quelle di oggi.
    quote = d[["odds_home", "odds_draw", "odds_away"]].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / quote
        pm = inv / inv.sum(axis=1, keepdims=True)
    ok = np.isfinite(pm).all(axis=1) & d["valida"].to_numpy()
    d["rps_mercato"] = np.nan
    if ok.any():
        d.loc[ok, "rps_mercato"] = rps(pm[ok], y[ok])

    d["matchday"] = int(matchday)
    return d.reindex(columns=COLONNE).sort_values(["match_date", "home_team"])


def aggiorna_riepilogo() -> pd.DataFrame:
    """
    Rifa' da zero il riepilogo cumulativo leggendo tutte le giornate chiuse.

    Da zero e non in append: i file di giornata sono la fonte, il riepilogo e'
    una vista. Ricalcolarlo tutte le volte costa niente e rende impossibile
    che i due divergano — che e' l'unico modo in cui un cumulativo scritto per
    aggiunte successive puo' sbagliare senza che nessuno se ne accorga.
    """
    files = sorted(rounds.ROUNDS.glob("round_*.csv"))
    if not files:
        return pd.DataFrame()

    righe = []
    for f in files:
        d = pd.read_csv(f)
        valide = d[d["valida"].astype(bool)] if "valida" in d.columns else d
        righe.append({
            "season": str(d["season"].iloc[0]),
            "matchday": int(d["matchday"].iloc[0]),
            "n_previsioni": len(d),
            "n_valide": len(valide),
            "rps": float(valide["rps"].mean()) if len(valide) else np.nan,
            "rps_mercato": float(valide["rps_mercato"].mean()) if len(valide) else np.nan,
            "accuratezza": float(valide["azzeccato"].mean()) if len(valide) else np.nan,
            "file": f.name,
        })

    tab = pd.DataFrame(righe).sort_values(["season", "matchday"]).reset_index(drop=True)
    # Cumulativo pesato sulle partite, non media di medie: una giornata con
    # tre previsioni valide non deve pesare quanto una da dieci.
    tab["n_cumulate"] = tab["n_valide"].cumsum()
    tab["rps_cumulativo"] = (
        (tab["rps"] * tab["n_valide"]).cumsum() / tab["n_valide"].cumsum()
    )
    tab.to_csv(rounds.RIEPILOGO, index=False)
    return tab


def stampa_giornata(d: pd.DataFrame) -> None:
    """Partita per partita: cosa era stato previsto, com'e' finita, quanto sbagliato."""
    if d.empty:
        return
    intest = (f"  {'partita':<28} {'1':>5} {'X':>5} {'2':>5}  {'esito':>5} "
              f"{'risultato':>9}  {'RPS':>7}  {'mercato':>7}")
    print(intest)
    print("  " + "-" * (len(intest) - 2))
    for _, r in d.iterrows():
        partita = f"{r['home_team']} - {r['away_team']}"
        if not bool(r["valida"]):
            nota = "esclusa (scritta dopo il fischio)" if pd.notna(r["FTR"]) else "senza risultato"
            print(f"  {partita:<28} {r['p_home']:>5.0%} {r['p_draw']:>5.0%} "
                  f"{r['p_away']:>5.0%}  {'-':>5} {'-':>9}  {nota}")
            continue
        risultato = f"{int(r['FTHG'])}-{int(r['FTAG'])}"
        segno = "ok " if bool(r["azzeccato"]) else "no "
        mercato = f"{r['rps_mercato']:.4f}" if pd.notna(r["rps_mercato"]) else "-"
        print(f"  {partita:<28} {r['p_home']:>5.0%} {r['p_draw']:>5.0%} "
              f"{r['p_away']:>5.0%}  {r['FTR']:>3} {segno} {risultato:>9}  "
              f"{r['rps']:>7.4f}  {mercato:>7}")


# ---------------------------------------------------------------------------

def run(matchday: int | None = None, dry_run: bool = False,
        skip_ingest: bool = False, verbose: bool = False) -> int:
    """Porta una giornata da giocata a chiusa. Restituisce il codice di uscita."""
    rounds.silenzia(verbose)
    adesso = pd.Timestamp.now(tz="UTC")
    rounds.titolo(f"CHIUSURA DI UNA GIORNATA  |  {quando(adesso)}  (ora italiana)")

    try:
        falliti = rounds.aggiorna_dati(STAGE, skip_ingest)
        rounds.ricostruisci()
    except rounds.PassoFallito as exc:
        log.error("%s", exc)
        return 1

    tab = rounds.stato_giornate()
    if matchday is None:
        scelta = rounds.da_chiudere(tab)
        if scelta is None:
            print()
            print(rounds.perche_niente_da_fare(tab, "chiudere"))
            rounds.riepilogo_due_righe(
                "niente: nessuna giornata predetta e interamente giocata da chiudere",
                "attendere i risultati mancanti e rilanciare",
            )
            return 0
        matchday = int(scelta["matchday"])
        season = str(scelta["season"])
    else:
        log.info("giornata forzata da riga di comando: %d", matchday)
        riga = tab[tab["matchday"] == matchday]
        if riga.empty:
            log.error("la giornata %d non esiste in calendario", matchday)
            return 1
        riga = riga.iloc[0]
        season = str(riga["season"])
        mancanti = int(riga["n_partite"] - riga["n_giocate"])
        if mancanti:
            # Anche forzata a mano, una giornata incompleta non si chiude: il
            # danno e' lo stesso, e --round serve a scegliere QUALE chiudere,
            # non a saltare il controllo che rende sensato l'archivio.
            print()
            print(f"La giornata {matchday} ha ancora {mancanti} partite senza "
                  f"risultato: non la chiudo.\nUn RPS archiviato su una giornata "
                  f"incompleta non sarebbe piu' correggibile.")
            rounds.riepilogo_due_righe(
                f"niente: giornata {matchday} incompleta ({mancanti} risultati mancanti)",
                "attendere i recuperi e rilanciare",
            )
            return 0

    percorso = rounds.file_giornata(season, matchday)
    if percorso.exists():
        print(f"\nLa giornata {matchday} e' gia' chiusa: {percorso.name} esiste.")
        print("Non la riscrivo: l'archivio di una giornata si scrive una volta sola.")
        rounds.riepilogo_due_righe(
            f"niente: giornata {matchday} gia' archiviata",
            "usa 'python -m src.rounds --status' per vedere cosa resta",
        )
        return 0

    d = costruisci_giornata(season, matchday)
    if d.empty:
        print(f"\nNessuna previsione in registro per la giornata {matchday}.")
        rounds.riepilogo_due_righe(
            f"niente: giornata {matchday} senza previsioni da chiudere",
            "predici con 'python -m src.predict_round' prima che si giochi",
        )
        return 0

    rounds.titolo(f"GIORNATA {matchday}  |  {len(d)} previsioni agganciate")
    stampa_giornata(d)

    valide = d[d["valida"].astype(bool)]
    senza_risultato = int(d["FTR"].isna().sum())
    if senza_risultato:
        # Non dovrebbe succedere: `da_chiudere` gia' pretende la giornata
        # completa. Se succede, e' una previsione su una partita che il
        # calendario non riconosce piu' (rinvio con data cambiata), e chiudere
        # comunque archivierebbe un buco.
        print(f"\n{senza_risultato} previsioni non hanno un risultato agganciato: "
              f"non chiudo la giornata.")
        print("Di solito e' un rinvio che ha cambiato la chiave della partita.")
        rounds.riepilogo_due_righe(
            f"niente: giornata {matchday} con {senza_risultato} risultati non agganciati",
            "controlla i nomi squadra con 'python -m src.normalize --report'",
        )
        return 0

    if not dry_run:
        rounds.ROUNDS.mkdir(parents=True, exist_ok=True)
        d.to_csv(percorso, index=False)
        log.info("giornata archiviata in %s", percorso)
        riepilogo = aggiorna_riepilogo()
    else:
        log.info("dry-run: la giornata NON e' stata archiviata")
        riepilogo = pd.DataFrame()

    _stampa_cumulativo(riepilogo, valide)

    try:
        esito = predict_mod.run(use_next=True, dry_run=True, quiet=True)
    except Exception as exc:
        log.warning("previsione della prossima giornata non disponibile: %s", exc)
        esito = predict_mod.Esito(model_version=predict_mod.MarketOnly().name)
    try:
        report = report_mod.build(esito, falliti=falliti,
                                  registro=report_mod.RIGENERATO,
                                  origine="python -m src.close_round")
        print(f"\n  report:   {report}")
    except Exception as exc:
        log.error("report non rigenerato: %s", exc)

    rimaste = tab[(tab["stato"] == rounds.GIOCATA) & (tab["n_predette"] > 0)
                  & (tab["matchday"] != matchday)]
    fatto = (f"giornata {matchday} "
             f"{'analizzata ma NON archiviata (dry-run)' if dry_run else 'chiusa e archiviata'}"
             f": {len(valide)} previsioni valide, RPS {valide['rps'].mean():.4f}")
    sospeso = (f"{len(rimaste)} altre giornate giocate da chiudere: rilancia"
               if len(rimaste) else
               "nessun'altra giornata da chiudere; predici la prossima con "
               "'python -m src.predict_round'")
    rounds.riepilogo_due_righe(fatto, sospeso)
    return 0


def _stampa_cumulativo(riepilogo: pd.DataFrame, valide: pd.DataFrame) -> None:
    """Il track record di produzione dopo questa chiusura."""
    rounds.titolo("TRACK RECORD DI PRODUZIONE")
    if not valide.empty:
        print(f"  questa giornata       RPS {valide['rps'].mean():.4f}"
              f"   accuratezza {valide['azzeccato'].mean():.0%}"
              f"   su {len(valide)} partite")
        if valide["rps_mercato"].notna().any():
            print(f"  quote registrate      RPS {valide['rps_mercato'].mean():.4f}"
                  f"   (de-vig proporzionale, stesso momento)")
    if riepilogo.empty:
        print("\n  (dry-run: il cumulativo non e' stato aggiornato)")
        return

    ultima = riepilogo.iloc[-1]
    delta = ultima["rps_cumulativo"] - config.TEST_RPS_REFERENCE
    print(f"\n  giornate chiuse       {len(riepilogo)}")
    print(f"  previsioni valide     {int(ultima['n_cumulate'])}")
    print(f"  RPS cumulativo        {ultima['rps_cumulativo']:.4f}  contro "
          f"{config.TEST_RPS_REFERENCE:.4f} del backtest ({delta:+.4f})")
    if ultima["n_cumulate"] < 100:
        print(f"  -> {int(ultima['n_cumulate'])} partite sono troppo poche perche' "
              f"quel numero significhi qualcosa.")
    print(f"\n  riepilogo: {rounds.RIEPILOGO}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Chiude la prima giornata predetta e interamente giocata"
    )
    ap.add_argument("--round", type=int, dest="matchday",
                    help="forza una giornata invece di dedurla")
    ap.add_argument("--dry-run", action="store_true",
                    help="fa tutto tranne archiviare la giornata")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="riusa i dati gia' scaricati, non tocca la rete")
    ap.add_argument("--verbose", action="store_true",
                    help="mostra tutti i log dei moduli chiamati")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    sys.exit(run(matchday=args.matchday, dry_run=args.dry_run,
                 skip_ingest=args.skip_ingest, verbose=args.verbose))


if __name__ == "__main__":
    main()
