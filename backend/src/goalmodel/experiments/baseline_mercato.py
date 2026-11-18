"""
Punto 4: la baseline di mercato si puo' migliorare con quello che c'e' in
produzione?

LA DOMANDA E PERCHE' NON E' UN TEST SUL MODELLO. Tutti i confronti del
progetto sono "contro il mercato", e il mercato oggi e' B365 in apertura con
de-vigging di Shin. Se esiste una stima migliore delle stesse quote — un
consenso fra piu' book, un de-vigging diverso — allora oggi i modelli vengono
confrontati con un avversario piu' debole di quello che sarebbe onesto. E' una
domanda sulla baseline, non sulle feature.

SI VALUTA IN VALIDAZIONE, NON SUL TEST. Anche una baseline scelta guardando il
test sarebbe una specifica scelta sul test. Le candidate si confrontano sulle
stagioni di validazione; sul test si guarda solo quella scelta, una volta.

LE CANDIDATE, DICHIARATE PRIMA
  prod        B365, Shin — la produzione. Riferimento.
  b365_*      B365 con i tre de-vigging alternativi: proporzionale, potenza,
              rapporto di quote. Stesso book, altro modo di togliere il margine.
  consenso_*  media fra B365, BW e Avg, de-viggati uno per uno con lo stesso
              metodo. Solo book presenti nello snapshot di produzione: Pinnacle
              migliorerebbe il backtest e sarebbe inutilizzabile il venerdi'.

NIENTE DA ADDESTRARE. M1 non si stima: legge i lambda dalle quote. La
valutazione e' quindi diretta, partita per partita, senza walk-forward — e il
confronto appaiato con bootstrap a cluster sulla giornata e' lo stesso di
tutti gli altri.

Uso:
    python -m goalmodel.experiments.baseline_mercato
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from .. import config, experiments
from ..evaluation.evaluate import KEYS, add_matchday, compare, paired_pairs
from ..features import market
from ..models.baseline import PRED_COLS, predictions_from_lambdas

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("baseline_mercato")

BOOK_1X2_B365 = ["B365"]
BOOK_OU_B365 = ["B365"]
BOOK_1X2_CONSENSO = market.BOOKS_CONSENSO
BOOK_OU_CONSENSO = ["B365", "Avg"]      # BW non quota l'over/under in storico

CANDIDATE = {
    "b365_proporzionale": (BOOK_1X2_B365, BOOK_OU_B365, "proporzionale"),
    "b365_potenza": (BOOK_1X2_B365, BOOK_OU_B365, "potenza"),
    "b365_odds_ratio": (BOOK_1X2_B365, BOOK_OU_B365, "odds_ratio"),
    "consenso_shin": (BOOK_1X2_CONSENSO, BOOK_OU_CONSENSO, "shin"),
    "consenso_proporzionale": (BOOK_1X2_CONSENSO, BOOK_OU_CONSENSO, "proporzionale"),
    "consenso_potenza": (BOOK_1X2_CONSENSO, BOOK_OU_CONSENSO, "potenza"),
}


def _previsione(base: pd.DataFrame, lam_h, lam_a, nome: str) -> pd.DataFrame:
    """M1 da due colonne di lambda: la stessa matrice dei risultati di tutti."""
    ok = pd.notna(lam_h) & pd.notna(lam_a)
    pred = pd.DataFrame(index=base.index, columns=PRED_COLS, dtype=float)
    if ok.any():
        pieni = predictions_from_lambdas(
            pd.Series(lam_h)[ok].to_numpy(float), pd.Series(lam_a)[ok].to_numpy(float),
            index=base.index[ok], rho=config.DC_RHO)
        pred.loc[pieni.index, PRED_COLS] = pieni
    out = base[KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR"]].copy()
    out["model"] = nome
    return pd.concat([out, pred], axis=1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Baseline di mercato alternative, in validazione")
    ap.parse_args()
    experiments.proteggi_produzione()
    logging.getLogger("market").setLevel(logging.WARNING)

    master = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    master["season"] = master["season"].astype(str)
    master = master[master["FTR"].notna()].reset_index(drop=True)
    master = add_matchday(master)
    val = master["season"].isin(config.VALIDATION_SEASONS).to_numpy()

    prod = market.build(master, save=False)
    tutte = [_previsione(master[val], prod.loc[val, "mkt_lambda_home"],
                         prod.loc[val, "mkt_lambda_away"], "prod B365 shin")]

    copertura = []
    for nome, (b1, bou, metodo) in CANDIDATE.items():
        alt = market.market_block_alternativo(master[val], b1, bou, metodo)
        tutte.append(_previsione(master[val], alt["lambda_home"],
                                 alt["lambda_away"], nome))
        copertura.append({"candidata": nome,
                          "book_1x2_medi": alt["n_book_1x2"].mean(),
                          "book_ou_medi": alt["n_book_ou"].mean()})
    preds = pd.concat(tutte, ignore_index=True)

    print(f"\nvalidazione: {config.VALIDATION_SEASONS}  (test non toccato)")
    print("\n=== BOOK MEDIATI PER RIGA ===")
    print(pd.DataFrame(copertura).round(2).to_string(index=False))

    print("\n=== RPS IN VALIDAZIONE ===")
    print(compare(preds, reference="prod B365 shin", floor="__nessuno__")
          [["RPS", "log_loss", "n"]].round(5).to_string())

    coppie = [(nome, "prod B365 shin") for nome in CANDIDATE]
    tab = paired_pairs(preds, coppie)
    print("\n=== CANDIDATA - PRODUZIONE (negativo = baseline migliore) ===")
    print(tab[["differenza", "ic_basso", "ic_alto", "conclusione"]].round(5).to_string())
    tab.reset_index().to_csv(experiments.percorso("riassunto_baseline_mercato.csv"),
                             index=False)


if __name__ == "__main__":
    main()
