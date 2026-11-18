"""
Il rho di Dixon-Coles per M1: rimettere i pareggi che la conversione toglie.

IL DIFETTO (riassunto_bias_gol.md). Le quote de-viggate prevedono il 25.8% di
pareggi, compatibile con il 28.0% osservato. M1 le converte in due lambda e
ricostruisce la matrice con due Poisson INDIPENDENTI (`config.DC_RHO = 0`):
ne escono il 24.1%, e lo scarto dall'osservato esclude lo zero. Il pareggio si
perde nella conversione, non nel mercato.

LA CORREZIONE. Un rho negativo alza 0-0 e 1-1 e abbassa 1-0 e 0-1. Le quattro
celle hanno tutte al piu' due gol e la correzione ne conserva la massa, quindi
l'over/under 2.5 — su cui i lambda del mercato sono stati invertiti — resta
IDENTICO: si sposta solo la distribuzione dentro l'1X2 e il gol-gol.

REGOLE, SCRITTE PRIMA DI GUARDARE.
  taratura  sulla sola VALIDAZIONE (2122, 2223), Serie A. Griglia di rho da
            -0.20 a 0.00 a passo 0.01 (il minimo ammissibile su tutto lo storico
            e' -0.245, `valid_rho_floor`). Si sceglie il rho che minimizza
            l'RPS medio: metrica primaria, regola 3. La log-verosimiglianza del
            risultato esatto si riporta, non decide.
  test      M1(rho scelto) contro M1(0) sulle 1140 partite del test, confronto
            appaiato con bootstrap a cluster sulla giornata. Si ADOTTA solo se
            entrambe:
              a) l'intervallo della differenza di RPS non sta tutto sopra zero
                 (non peggiora l'1X2);
              b) l'intervallo dello scarto sui pareggi (osservati - previsti)
                 contiene lo zero (il difetto e' corretto).
            Non e' un blocco di feature e non entra nel conto m dei blocchi:
            e' la calibrazione di una trasformazione deterministica.

Scrive solo in `experiments/output/`.

Uso:
    python -m goalmodel.experiments.rho_mercato --tara
    python -m goalmodel.experiments.rho_mercato --misura --rho -0.08
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from .. import config, experiments
from ..evaluation.evaluate import (
    KEYS,
    PROB_COLS,
    cluster_bootstrap,
    load_dataset,
    log_loss,
    outcome_index,
    paired_pairs,
    rps,
)
from ..models.baseline import MarketDirect, predictions_from_lambdas, score_matrix

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("rho_mercato")

GRIGLIA = np.round(np.arange(-0.20, 0.0001, 0.01), 2)


def _partite(stagioni: list[str]) -> pd.DataFrame:
    df = load_dataset()
    df = df[df["league"].isin(config.LEAGUES_PRODUZIONE) & df["season"].isin(stagioni)]
    return df.dropna(subset=["FTR", "FTHG", "FTAG", "mkt_lambda_home", "mkt_lambda_away"]
                     ).reset_index(drop=True)


def _cluster(df: pd.DataFrame) -> np.ndarray:
    return (df["season"].astype(str) + "-" + df["matchday"].astype(str)).to_numpy()


def _m1(df: pd.DataFrame, rho: float) -> pd.DataFrame:
    return predictions_from_lambdas(df["mkt_lambda_home"].to_numpy(float),
                                    df["mkt_lambda_away"].to_numpy(float),
                                    index=df.index, rho=rho)


def _loglik_esatto(df: pd.DataFrame, rho: float) -> float:
    mat = score_matrix(df["mkt_lambda_home"].to_numpy(float),
                       df["mkt_lambda_away"].to_numpy(float), rho=rho)
    i = np.minimum(df["FTHG"].to_numpy(int), mat.shape[1] - 1)
    j = np.minimum(df["FTAG"].to_numpy(int), mat.shape[2] - 1)
    return float(np.log(mat[np.arange(len(df)), i, j]).mean())


def _scarto_pareggi(df: pd.DataFrame, p: pd.DataFrame) -> dict:
    d = (df["FTR"] == "D").to_numpy(float) - p["p_draw"].to_numpy(float)
    return cluster_bootstrap(d, _cluster(df))


def tara() -> None:
    experiments.proteggi_produzione()
    df = _partite(config.VALIDATION_SEASONS)
    y = outcome_index(df["FTR"])
    righe = []
    for rho in GRIGLIA:
        p = _m1(df, rho)
        pr = p[PROB_COLS].to_numpy(float)
        righe.append({"rho": rho, "RPS": rps(pr, y).mean(), "log_loss": log_loss(pr, y).mean(),
                      "loglik_esatto": _loglik_esatto(df, rho),
                      "p_draw": p["p_draw"].mean(), "oss_draw": (df["FTR"] == "D").mean(),
                      "p_over25": p["p_over25"].mean()})
    tab = pd.DataFrame(righe)
    print(f"\n=== VALIDAZIONE {config.VALIDATION_SEASONS}, {len(df)} partite ===")
    print(tab.round(6).to_string(index=False))
    scelto = tab.loc[tab["RPS"].idxmin(), "rho"]
    bordo = scelto in (GRIGLIA.min(), GRIGLIA.max())
    print(f"\nrho scelto (min RPS): {scelto:+.2f}{'  ATTENZIONE: sul bordo della griglia' if bordo else ''}")
    print(f"rho al massimo della log-verosimiglianza esatta: "
          f"{tab.loc[tab['loglik_esatto'].idxmax(), 'rho']:+.2f}")
    tab.to_csv(experiments.percorso("riassunto_rho_validazione.csv"), index=False)


def misura(rho: float) -> None:
    experiments.proteggi_produzione()
    df = _partite(config.TEST_SEASONS)
    base = df[KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR"]]

    modelli = {
        "M1 rho=0": _m1(df, 0.0),
        f"M1 rho={rho:+.2f}": _m1(df, rho),
        "M1b diretto": MarketDirect().predict(df),
    }
    preds = pd.concat([pd.concat([base, p], axis=1).assign(model=nome)
                       for nome, p in modelli.items()], ignore_index=True)

    nuovo = f"M1 rho={rho:+.2f}"
    confronti = paired_pairs(preds, [(nuovo, "M1 rho=0"), (nuovo, "M1b diretto"),
                                     ("M1 rho=0", "M1b diretto")])
    print(f"\n=== TEST {config.TEST_SEASONS}, {len(df)} partite: differenza di RPS ===")
    print(confronti[["differenza", "ic_basso", "ic_alto", "conclusione"]].round(5).to_string())

    print("\n=== Pareggi: osservati - previsti ===")
    righe = []
    for nome, p in modelli.items():
        r = _scarto_pareggi(df, p)
        righe.append({"modello": nome, "p_draw": p["p_draw"].mean(),
                      "osservati": (df["FTR"] == "D").mean(), "scarto": r["differenza"],
                      "ic_basso": r["ic_basso"], "ic_alto": r["ic_alto"]})
    pareggi = pd.DataFrame(righe)
    print(pareggi.round(5).to_string(index=False))

    print("\n=== Invarianti: over 2.5 e gol attesi non devono muoversi ===")
    for nome in ("M1 rho=0", nuovo):
        p = modelli[nome]
        print(f"  {nome:<12} over2.5 {p['p_over25'].mean():.6f}   gol-gol {p['p_btts'].mean():.4f}")

    print(f"\nloglik risultato esatto: rho=0 {_loglik_esatto(df, 0.0):.5f}   "
          f"rho={rho:+.2f} {_loglik_esatto(df, rho):.5f}")

    a = confronti.loc[f"{nuovo}  -  M1 rho=0", "ic_basso"] <= 0
    riga = pareggi[pareggi["modello"] == nuovo].iloc[0]
    b = riga["ic_basso"] <= 0 <= riga["ic_alto"]
    print(f"\nREGOLA a) RPS non peggiora: {'SI' if a else 'NO'}   "
          f"b) pareggi corretti: {'SI' if b else 'NO'}   ->  "
          f"{'ADOTTARE' if a and b else 'NON ADOTTARE'}")
    pd.concat([confronti.reset_index(), pareggi], ignore_index=True).to_csv(
        experiments.percorso("riassunto_rho_test.csv"), index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="rho di Dixon-Coles per M1")
    ap.add_argument("--tara", action="store_true", help="griglia di rho sulla validazione")
    ap.add_argument("--misura", action="store_true", help="il rho scelto sul test")
    ap.add_argument("--rho", type=float, help="il rho da misurare (da --tara)")
    args = ap.parse_args()
    if args.tara:
        tara()
    elif args.misura:
        if args.rho is None:
            ap.error("--misura richiede --rho, scelto con --tara")
        misura(args.rho)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
