"""
La taratura: cercare gli iperparametri, il peso della miscela, la half-life.

PERCHE' STA IN `evaluation/` E NON ACCANTO AI MODELLI. Tarare e' misurare: si
lancia un walk-forward di validazione, si calcola un RPS, si sceglie il minimo.
Il modello e' l'oggetto della misura, non chi la fa.

Finche' queste funzioni vivevano in `models/gbm.py` e `models/dixon_coles.py`
importavano `evaluation` **dentro le funzioni**, per evitare il ciclo:
`evaluation` importa `models` al livello del modulo, quindi `models` non puo'
importare `evaluation` a sua volta. Era l'unico import all'indietro del
progetto, scritto a chiare lettere in `CLAUDE.md` come debito da sciogliere:
"si risolve spostando la taratura in `evaluation/`, non aggiungendone altri".
Questo file e' quello spostamento.

I COMANDI NON CAMBIANO. `goalmodel gbm --tune`, `goalmodel gbm --importance`,
`goalmodel dixon-coles --tune` e `--check` fanno esattamente quello che
facevano: `cli.py` li manda qui invece che dentro `models/`. Cambia il file,
non la riga che si scrive.

LA TARATURA NON GUARDA MAI IL TEST. Gira sulle stagioni di validazione
(`config.VALIDATION_SEASONS`). Scegliere un iperparametro guardando il test
set significherebbe consumarlo, e il progetto ha una sola soglia da battere.
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from .. import config
from ..features.dataset import load_dataset
from ..models import dixon_coles as dc
from ..models.gbm import (
    MKT_LAMBDA,
    VARIANTS,
    PoissonGBM,
    feature_importance,
    on_boundary,
    sample_configs,
    space_for,
)
from .evaluate import PROB_COLS, outcome_index, rps, walk_forward

log = logging.getLogger("taratura")


def _score_config(df: pd.DataFrame, variant: str, cfg: dict,
                  validation_seasons: list[str], stride: int) -> dict:
    """Una configurazione, un walk-forward di validazione, un RPS."""
    import logging as _logging

    # Nei processi figli il log del walk-forward e' solo rumore interlacciato.
    _logging.getLogger("evaluate").setLevel(_logging.WARNING)

    model = VARIANTS[variant](cfg)
    preds = walk_forward(df, [model], test_seasons=validation_seasons, stride=stride)
    ok = preds.dropna(subset=PROB_COLS)
    return {
        "variante": variant,
        **cfg,
        "RPS_validazione": float(rps(ok[PROB_COLS].to_numpy(dtype=float),
                                     outcome_index(ok["FTR"])).mean()),
        "alberi_medi": float(np.mean(model.best_iters_)) if model.best_iters_ else np.nan,
    }


def tune(
    df: pd.DataFrame,
    n_configs: int = 24,
    stride: int = 3,
    validation_seasons: list[str] | None = None,
    seed: int = 0,
    n_workers: int = 8,
    variants: tuple[str, ...] = tuple(VARIANTS),
) -> pd.DataFrame:
    """
    Ricerca casuale sulla VALIDAZIONE, per ciascuna variante.

    Le configurazioni girano in parallelo su processi separati, con LightGBM a
    un thread ciascuno. E' l'assetto giusto per questi dati: con 3000 righe il
    multithreading dentro LightGBM rende poco perche' domina l'attrito dei
    thread, mentre le configurazioni sono indipendenti e riempiono i core.
    """
    from joblib import Parallel, delayed

    validation_seasons = list(validation_seasons or config.VALIDATION_SEASONS)
    jobs = [
        (v, c)
        for v in variants
        for c in sample_configs(n_configs, seed=seed, space=space_for(v))
    ]
    # `len(jobs)` e non un conteggio a parte: la variante ancorata usa uno
    # spazio esteso, quindi "n_configs x varianti" e' un numero che puo' non
    # corrispondere ai run veri. Prima qui si ricampionava lo spazio comune
    # solo per contarlo, e il log diceva la cifra sbagliata (audit B9).
    log.info("%d run su %d varianti, stride %d, %d processi",
             len(jobs), len(variants), stride, n_workers)

    rows = Parallel(n_jobs=n_workers, verbose=10)(
        delayed(_score_config)(df, v, c, validation_seasons, stride) for v, c in jobs
    )
    return pd.DataFrame(rows)


def tune_blend_weight(
    df: pd.DataFrame,
    gbm_params: dict | None = None,
    grid: np.ndarray | None = None,
    validation_seasons: list[str] | None = None,
) -> pd.DataFrame:
    """
    Peso della miscela logaritmica, stimato sulla SOLA validazione.

    Si fa un unico walk-forward di validazione con M4 senza mercato, si
    tengono i suoi lambda, e si cerca il peso a griglia: non serve
    riaddestrare per ogni peso, perche' il peso non entra nell'addestramento.
    """
    from .baseline import predictions_from_lambdas

    gbm_params = gbm_params or config.GBM_PARAMS_NO_MARKET
    grid = np.arange(0.0, 1.01, 0.05) if grid is None else grid
    validation_seasons = list(validation_seasons or config.VALIDATION_SEASONS)

    preds = walk_forward(
        df, [PoissonGBM(use_market=False, **gbm_params)],
        test_seasons=validation_seasons,
    )
    # walk_forward tiene solo chiavi, esito e previsioni: i lambda di mercato
    # vanno riagganciati dal dataset.
    keys = config.JOIN_KEYS
    preds = preds.merge(df[keys + list(MKT_LAMBDA)], on=keys, how="left", validate="many_to_one")
    ok = preds.dropna(subset=["lambda_home", "lambda_away", *MKT_LAMBDA]).reset_index(drop=True)
    y = outcome_index(ok["FTR"])
    g = np.log(ok[["lambda_home", "lambda_away"]].to_numpy(dtype=float))
    m = np.log(ok[list(MKT_LAMBDA)].to_numpy(dtype=float))

    rows = []
    for w in grid:
        lam = np.exp(w * g + (1 - w) * m)
        p = predictions_from_lambdas(lam[:, 0], lam[:, 1], index=ok.index)
        rows.append({"peso": float(w),
                     "RPS_validazione": float(rps(p[PROB_COLS].to_numpy(dtype=float), y).mean())})
    tab = pd.DataFrame(rows).sort_values("RPS_validazione").reset_index(drop=True)
    log.info("peso scelto: %.2f (RPS %.6f); mercato puro w=0 -> %.6f, GBM puro w=1 -> %.6f",
             tab.loc[0, "peso"], tab.loc[0, "RPS_validazione"],
             float(tab.loc[tab["peso"] == 0.0, "RPS_validazione"].iloc[0]),
             float(tab.loc[tab["peso"] == 1.0, "RPS_validazione"].iloc[0]))
    return tab


def tune_halflife(
    df: pd.DataFrame,
    grid: list[float] | None = None,
    validation_seasons: list[str] | None = None,
) -> pd.DataFrame:
    """
    Tara la half-life sulla VALIDAZIONE, con lo stesso walk-forward del test.

    Il test set non viene mai toccato: se lo si usasse per scegliere fra cinque
    half-life, il risultato riportato sarebbe il massimo di cinque tentativi e
    non una stima onesta.
    """

    grid = grid or config.DC_HALFLIFE_GRID
    validation_seasons = validation_seasons or config.VALIDATION_SEASONS

    rows = []
    for hl in grid:
        preds = walk_forward(df, [dc.DixonColes(halflife_days=hl)], test_seasons=validation_seasons)
        ok = preds.dropna(subset=PROB_COLS)
        score = rps(ok[PROB_COLS].to_numpy(dtype=float), outcome_index(ok["FTR"])).mean()
        rows.append({"half_life_giorni": hl, "RPS_validazione": float(score), "n": len(ok)})
        log.info("half-life %3g giorni -> RPS validazione %.6f", hl, score)

    tab = pd.DataFrame(rows).sort_values("RPS_validazione").reset_index(drop=True)
    log.info("scelta: half-life %g giorni", tab.loc[0, "half_life_giorni"])
    return tab


def main_gbm() -> None:
    ap = argparse.ArgumentParser(description="LightGBM Poisson a due gol")
    ap.add_argument("--tune", action="store_true", help="ricerca iperparametri sulla validazione")
    ap.add_argument("--blend", action="store_true", help="peso della miscela log sulla validazione")
    ap.add_argument("--importance", action="store_true", help="importanza feature di M4 senza mercato")
    ap.add_argument("--anchored", action="store_true",
                    help="con --importance: usa M5 ancorato invece di M4. E' la "
                         "vista giusta per un blocco nuovo, che puo' spiegare "
                         "solo il residuo dal mercato")
    ap.add_argument("--configs", type=int, default=24, help="quante configurazioni provare")
    ap.add_argument("--stride", type=int, default=3, help="giornate per blocco durante la ricerca")
    ap.add_argument("--workers", type=int, default=8, help="processi paralleli")
    ap.add_argument("--variants", default=",".join(VARIANTS), help="varianti da tarare")
    args = ap.parse_args()

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 50)

    if args.blend:
        tab = tune_blend_weight(load_dataset())
        print(tab.head(8).to_string(index=False))
        print("\ncurva completa (peso, RPS):")
        print(tab.sort_values("peso").to_string(index=False))
        return

    if args.importance:
        tab = feature_importance(load_dataset(), anchored=args.anchored)
        quale = "M5 ANCORATO AL MERCATO" if args.anchored else "M4 SENZA MERCATO"
        print(f"=== IMPORTANZA PER GUADAGNO, {quale} ===")
        print("media su 3 stagioni di test x 2 lati; quota del guadagno totale\n")
        print(tab.head(20).round(4).to_string())
        print("\nsomma delle prime 10: %.1f%%" % (100 * tab["quota_media"].head(10).sum()))
        famiglie = tab.copy()
        famiglie["famiglia"] = [
            c.split("_", 1)[1].replace("_ewm", "") if c.startswith(("home_", "away_", "diff_")) else c
            for c in famiglie.index
        ]
        print("\n=== AGGREGATO PER STATISTICA (le tre viste casa/fuori/diff insieme) ===")
        print(famiglie.groupby("famiglia")["quota_media"].sum()
              .sort_values(ascending=False).head(12).round(4).to_string())
        return

    if not args.tune:
        ap.print_help()
        return

    variants = tuple(v.strip() for v in args.variants.split(",") if v.strip())
    tab = tune(load_dataset(), n_configs=args.configs, stride=args.stride,
               n_workers=args.workers, variants=variants)
    out = config.PROCESSED / "gbm_tuning.parquet"
    tab.to_parquet(out, index=False)

    for variant in variants:
        sub = tab[tab["variante"] == variant].sort_values("RPS_validazione")
        print(f"\n=== variante '{variant}': migliori 5 di {len(sub)} ===")
        print(sub.head(5).to_string(index=False))
        best = sub.iloc[0].drop(["variante", "RPS_validazione", "alberi_medi"]).to_dict()
        bordo = on_boundary(best, space_for(variant))
        print(f"scelta: {best}")
        print(f"alberi scelti dall'arresto anticipato: ~{sub.iloc[0]['alberi_medi']:.0f}")
        if bordo:
            print(f"ATTENZIONE, parametri sul bordo dello spazio: {', '.join(bordo)}")
            print("Estendere quei parametri prima di fidarsi della scelta.")
        else:
            print("nessun parametro sul bordo: il minimo e' interno")

    print(f"\nscritto {out.name}")


def main_dixon_coles() -> None:
    ap = argparse.ArgumentParser(description="Dixon-Coles con decadimento temporale")
    ap.add_argument("--check", action="store_true", help="verifica il gradiente analitico")
    ap.add_argument("--tune", action="store_true", help="tara la half-life sulla validazione")
    args = ap.parse_args()

    if args.check:
        dc._check_gradient()
        return
    if args.tune:
        print(tune_halflife(load_dataset()).to_string(index=False))
        return
    ap.print_help()
