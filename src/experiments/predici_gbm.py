"""
La giornata in arrivo predetta con M5, il GBM ancorato al mercato — FUORI dal
percorso di produzione e SENZA scrivere nel registro.

COSA FA E COSA NON FA. Riusa `src/predict.py` cosi' com'e', passandogli un
modello diverso e `dry_run=True`: stesso calendario, stesse quote, stesse
feature, stesse protezioni sul calcio d'inizio. Nessuna riga entra in
`predictions_log.csv`, nessun file di produzione viene toccato, e la guardia di
`experiments` lo impone invece di limitarsi a promettere.

PERCHE' NON E' IL MODELLO DI PRODUZIONE, E PERCHE' NON DEVE DIVENTARLO OGGI.
Sul test set M5 e' **indistinguibile dal mercato**: +0.00011 di RPS con IC
[-0.00056, +0.00077]. Non e' peggiore, non e' migliore. Il criterio di
promozione scritto in CLAUDE.md chiede che un modello batta il mercato con
intervallo che non tocca lo zero, dopo correzione per confronti multipli:
M5 non lo fa, quindi `predict_round` resta su M1. Mettere in produzione un
modello misurato indistinguibile significherebbe buttare via la misura.

A COSA SERVE ALLORA. A guardare **dove** M5 si discosta dal mercato su partite
vere. E' la stessa idea della sezione 3 del report — diagnostica, non un
segnale di scommessa — ma con il modello giusto: il report usa M4 senza
mercato, che parte da zero e diverge ovunque; M5 parte dal mercato e si muove
solo dove le feature di forma dicono qualcosa. Uno scarto grande qui e' una
partita da capire, non un'occasione da giocare. Il modello non produce valore
atteso positivo: vedi la sezione dedicata in CLAUDE.md.

QUALE M5. `M5MediaSemi(["BASE"])`: le 52 colonne congelate di BASE, media
geometrica dei lambda di cinque semi. I set sperimentali non entrano —
GIOCATORI e' provvisorio e per una partita futura le assenze non sono nemmeno
nel dataset (servirebbe `ingest --stage missing` sul turno in arrivo), quindi
un M5 con quelle colonne girerebbe con NaN dove in addestramento aveva dati.
La media dei semi e' la stima dichiarata: un seme solo sposta i lambda di qualche
millesimo, e non ha senso che la previsione mostrata dipenda da quale.

Uso:
    python -m src.experiments.predici_gbm            # prima giornata utile
    python -m src.experiments.predici_gbm --matchday 5
    python -m src.experiments.predici_gbm --semi 0   # un seme solo, piu' veloce
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from .. import config, experiments, predict as predict_mod
from ..evaluate import load_dataset
from ..models.baseline import fair_odds, predictions_from_lambdas
from .modelli import M5MediaSemi

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("predici_gbm")


def addestra(semi: tuple[int, ...], cutoff: pd.Timestamp | None = None) -> M5MediaSemi:
    """
    M5 addestrato su tutto lo storico giocato, come farebbe il walk-forward.

    Stesso perimetro della valutazione: partite con risultato, burn-in escluso.
    Non c'e' niente da tarare — gli iperparametri sono quelli di
    `config.GBM_PARAMS_ANCHORED`, fissati sulla validazione — e il numero di
    alberi lo decide l'arresto anticipato sulla coda del training.

    `cutoff` serve con `--as-of`: rifare una giornata gia' giocata addestrando
    su tutto lo storico significherebbe addestrare anche su quella giornata, e
    la "previsione" conterrebbe il risultato che dice di prevedere. Il taglio
    e' sulla data, come nel walk-forward, non sulla giornata.
    """
    df = load_dataset()
    train = df[df["FTR"].notna() & ~df["season"].isin(config.BURN_IN_SEASONS)]
    if cutoff is not None:
        limite = cutoff.tz_convert(None) if cutoff.tz is not None else cutoff
        train = train[pd.to_datetime(train["date"]) < limite]
        log.info("taglio del training a %s (--as-of)", limite.date())
    log.info("addestro su %d partite giocate, %d semi", len(train), len(semi))
    modello = M5MediaSemi(["BASE"], semi=semi).fit(train)
    alberi = [m.best_iters_ for m in modello.modelli if m.model_home_ is not None]
    log.info("alberi per seme (casa, fuori): %s", alberi)
    if not alberi:
        raise SystemExit("modello non addestrato: training troppo corto")
    return modello


def confronta(preds: pd.DataFrame) -> pd.DataFrame:
    """
    M5 accanto a M1 sulle stesse righe, con lo scarto.

    M1 non si ricalcola con un secondo giro di `predict.run`: i lambda del
    mercato sono gia' nelle colonne `mkt_lambda_*` del frame, e M1 *e'* la
    matrice dei risultati costruita su quei due numeri. Rifare la previsione
    da capo aggiungerebbe solo un'occasione di divergere.
    """
    m1 = predictions_from_lambdas(
        preds["mkt_lambda_home"].to_numpy(float),
        preds["mkt_lambda_away"].to_numpy(float),
        index=preds.index, rho=config.DC_RHO,
    )
    out = pd.DataFrame({
        "data": pd.to_datetime(preds["date"]).dt.strftime("%a %d/%m"),
        "partita": preds["home_team"] + " - " + preds["away_team"],
        "1 mkt": m1["p_home"], "1 M5": preds["p_home"],
        "X mkt": m1["p_draw"], "X M5": preds["p_draw"],
        "2 mkt": m1["p_away"], "2 M5": preds["p_away"],
        "lam casa mkt": m1["lambda_home"], "lam casa M5": preds["lambda_home"],
        "lam fuori mkt": m1["lambda_away"], "lam fuori M5": preds["lambda_away"],
    })
    out["scarto max 1X2"] = (
        pd.concat([(out["1 M5"] - out["1 mkt"]).abs(),
                   (out["X M5"] - out["X mkt"]).abs(),
                   (out["2 M5"] - out["2 mkt"]).abs()], axis=1).max(axis=1)
    )
    # La quota equa di M5 accanto a quella reale del book: serve a LEGGERE la
    # quota, non a prometterle un vantaggio. Con M1 la reale sta sempre sotto
    # l'equa, per aritmetica del margine.
    out["equa 1 M5"] = fair_odds(preds["p_home"])
    out["quota 1"] = preds["odds_home"]
    return out.sort_values("scarto max 1X2", ascending=False)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="La giornata in arrivo secondo M5 (GBM ancorato). Non scrive nel registro.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--matchday", type=int, help="giornata da predire")
    g.add_argument("--next", action="store_true", help="prima giornata utile (default)")
    ap.add_argument("--semi", type=int, nargs="+", default=[0, 1, 2, 3, 4],
                    help="semi da mediare (default: cinque)")
    ap.add_argument("--team", help="filtra su una squadra")
    ap.add_argument("--as-of", help="finge che 'adesso' sia questa data UTC (YYYY-MM-DD), "
                                    "per rifare una giornata gia' giocata con le quote di "
                                    "allora. Resta un dry-run: non scrive nemmeno nel "
                                    "file di backfill")
    args = ap.parse_args()

    experiments.proteggi_produzione()
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)

    as_of = pd.Timestamp(args.as_of, tz="UTC") if args.as_of else None
    modello = addestra(tuple(args.semi), cutoff=as_of)
    esito = predict_mod.run(
        matchday=args.matchday,
        team=args.team,
        use_next=args.matchday is None,
        as_of=as_of,
        model=modello,
        dry_run=True,      # non negoziabile: qui non si scrive nel registro
        quiet=True,
    )

    if esito.preds.empty:
        print("\nnessuna partita predicibile: "
              f"{len(esito.skipped)} senza quote di apertura complete.")
        if not esito.skipped.empty:
            for _, r in esito.skipped.iterrows():
                print(f"  {r['home_team']} - {r['away_team']}: manca {r.get('manca', 'quote')}")
        return

    tab = confronta(esito.preds)
    print(f"\n=== GIORNATA {esito.matchday} — M5 GBM ancorato contro il mercato (M1) ===")
    print(f"    {esito.model_version}\n")
    print(tab.round(3).to_string(index=False))
    print(f"\nscarto massimo sull'1X2: {tab['scarto max 1X2'].max():.4f} "
          f"(mediano {tab['scarto max 1X2'].median():.4f})")
    print("\nDIAGNOSTICA, NON UN SEGNALE DI SCOMMESSA. Sul test set M5 e'"
          " indistinguibile dal mercato\n(+0.00011, IC [-0.00056, +0.00077]):"
          " uno scarto grande e' una partita da capire,\nnon un'occasione."
          " Il registro resta su M1 e questa passata non lo ha toccato.")

    dst = experiments.percorso(
        f"previsioni_m5_{esito.preds['season'].iloc[0]}_{esito.matchday:02d}.csv")
    tab.to_csv(dst, index=False)
    print(f"\nscritto {dst}")


if __name__ == "__main__":
    main()
