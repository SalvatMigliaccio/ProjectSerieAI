"""
Punto 3: ridurre la collinearita' di BASE. Stessa informazione, altra forma.

PERCHE'. BASE ha 52 colonne e molte misurano la stessa cosa. Alcune per
costruzione: i punti attesi delle due squadre sommano quasi a una costante,
quindi `xpts_for` e `xpts_against` correlano a 0.997 e una e' l'altra
ribaltata; `home_matches_season` e `away_matches_season` a 0.999, perche' due
squadre in campo hanno giocato lo stesso numero di partite. Con colonne cosi'
LightGBM sceglie fra gemelle quasi a caso, e l'importanza si spalma in modo
arbitrario: e' uno dei motivi per cui una diagnosi come il 20:1 del blocco B
va presa con le molle.

NON E' UN TEST DI IPOTESI. Non si aggiunge informazione, si cambia la
rappresentazione di quella che c'e' gia'. Si valuta sulla VALIDAZIONE
(2021/22 e 2022/23), mai sul test: una rappresentazione scelta guardando il
test consumerebbe un confronto come qualsiasi altro blocco.

LE DUE RAPPRESENTAZIONI, CON LE REGOLE SCRITTE PRIMA
  selezione   si scartano le colonne con |r| > SOGLIA con una colonna gia'
              tenuta. Soglia dichiarata: 0.95. L'ordine di visita e' quello di
              BASE, quindi a parita' si tiene la vista `home` sulla `away` e
              `for` su `against`: una regola arbitraria ma fissata, non scelta
              dopo aver visto quale versione rende di piu'.
  componenti  BASE si divide nei suoi blocchi naturali — una statistica, sei
              colonne (casa/trasferta/differenza x fatti/subiti) — e ogni
              blocco si comprime con le componenti principali che spiegano il
              VARIANZA_PCA della sua varianza. I blocchi restano separati:
              una componente che mescolasse tiri e punti attesi sarebbe
              impossibile da leggere, e l'interpretabilita' e' meta' del motivo
              per cui si fa.

TUTTO SI STIMA PRIMA DELLA VALIDAZIONE. Correlazioni, medie, deviazioni
standard e componenti si calcolano sulle sole stagioni precedenti alla prima
di validazione, esclusi burn-in e stagione in corso. Un'esplorazione
precedente aveva infilato la 2026/27 nel "training": per la validazione e'
futuro, e anche una statistica senza target calcolata sul futuro e' leakage.

Scrive solo in `experiments/output/`.

Uso:
    python -m src.experiments.collinearita --descrivi
    python -m src.experiments.collinearita --misura
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from .. import config, experiments
from ..evaluate import KEYS, load_dataset, paired_pairs, walk_forward
from ..features import sets as sets_mod
from ..models.baseline import PRED_COLS
from .modelli import M5Colonne, M5Set

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("collinearita")

SOGLIA = 0.95
VARIANZA_PCA = 0.95


def stagioni_di_stima(df: pd.DataFrame) -> list[str]:
    """Le stagioni PRIMA della prima di validazione, senza burn-in ne' corrente."""
    prima_val = min(config.VALIDATION_SEASONS)
    return sorted(
        s for s in df["season"].unique()
        if s < prima_val
        and s not in config.BURN_IN_SEASONS
        and s != config.CURRENT_SEASON
    )


def seleziona(df: pd.DataFrame) -> list[str]:
    """Selezione per correlazione, con la soglia e l'ordine dichiarati."""
    base = list(sets_mod.SETS["BASE"].colonne)
    stima = df[df["season"].isin(stagioni_di_stima(df))]
    corr = stima[base].corr().abs()
    tenute: list[str] = []
    for c in base:
        if all(corr.loc[c, t] <= SOGLIA for t in tenute):
            tenute.append(c)
    return tenute


def blocchi_base() -> dict[str, list[str]]:
    """BASE diviso per statistica: le sei viste di ciascuna, piu' i contatori."""
    base = list(sets_mod.SETS["BASE"].colonne)
    blocchi: dict[str, list[str]] = {}
    for c in base:
        if c.endswith("_ewm"):
            stat = c.split("_", 1)[1].rsplit("_", 2)[0]
        else:
            stat = "contatori"
        blocchi.setdefault(stat, []).append(c)
    return blocchi


def componenti(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """
    Componenti principali per blocco, stimate prima della validazione.

    Una riga con anche un solo NaN nel blocco riceve componenti NaN, invece di
    un'imputazione: in BASE i NaN arrivano tutti insieme — la prima partita di
    una squadra non ha storico in nessuna statistica — e LightGBM sa trattarli
    come "non so". Imputarli alla media gli toglierebbe proprio quel segnale.
    """
    stima = df[df["season"].isin(stagioni_di_stima(df))]
    uscita = pd.DataFrame(index=df.index)
    tenute_per_blocco: dict[str, int] = {}

    for nome, cols in blocchi_base().items():
        x_stima = stima[cols].dropna()
        media, sd = x_stima.mean(), x_stima.std(ddof=0).replace(0, 1.0)
        z = ((x_stima - media) / sd).to_numpy()
        # SVD invece di una libreria esterna: sono sei colonne, e cosi' ogni
        # passaggio e' leggibile.
        _, s, vt = np.linalg.svd(z - z.mean(axis=0), full_matrices=False)
        quota = np.cumsum(s ** 2) / np.sum(s ** 2)
        k = int(np.searchsorted(quota, VARIANZA_PCA) + 1)
        tenute_per_blocco[nome] = k

        tutto = (df[cols] - media) / sd
        completo = tutto.notna().all(axis=1)
        proiezione = np.full((len(df), k), np.nan)
        proiezione[completo.to_numpy()] = tutto[completo].to_numpy() @ vt[:k].T
        for i in range(k):
            uscita[f"pc_{nome}_{i + 1}"] = proiezione[:, i]
    return uscita, tenute_per_blocco


def descrivi() -> None:
    df = load_dataset()
    stima = stagioni_di_stima(df)
    print(f"stagioni di stima: {stima}")
    print(f"validazione: {config.VALIDATION_SEASONS}  (test mai toccato: {config.TEST_SEASONS})")

    tenute = seleziona(df)
    tolte = [c for c in sets_mod.SETS["BASE"].colonne if c not in tenute]
    print(f"\n=== SELEZIONE |r| > {SOGLIA} ===")
    print(f"BASE {len(sets_mod.SETS['BASE'].colonne)} -> {len(tenute)} colonne")
    print("tolte:", tolte)

    pc, per_blocco = componenti(df)
    print(f"\n=== COMPONENTI PRINCIPALI ({VARIANZA_PCA:.0%} della varianza, per blocco) ===")
    for nome, k in per_blocco.items():
        print(f"  {nome:<10} {len(blocchi_base()[nome])} colonne -> {k} componenti")
    print(f"totale: {pc.shape[1]} componenti al posto di "
          f"{len(sets_mod.SETS['BASE'].colonne)} colonne")


def misura(semi: tuple[int, ...] = (0, 1, 2)) -> None:
    """
    Walk-forward sulla sola validazione: BASE, BASE selezionato, BASE in PCA.

    Tre semi per variante: su un effetto piccolo, un seme solo potrebbe far
    sembrare migliore una rappresentazione per pura varianza di LightGBM.
    """
    experiments.proteggi_produzione()
    df = load_dataset()
    tenute = seleziona(df)
    pc, _ = componenti(df)
    df = pd.concat([df, pc], axis=1)

    modelli = []
    for s in semi:
        modelli += [
            M5Set(["BASE"], seed=s, etichetta="BASE"),
            M5Colonne(tenute, seed=s, etichetta=f"selezione{len(tenute)}"),
            M5Colonne(list(pc.columns), seed=s, etichetta=f"pca{pc.shape[1]}"),
        ]
    preds = walk_forward(df, modelli, test_seasons=config.VALIDATION_SEASONS)
    preds.to_parquet(experiments.percorso("wf_collinearita_validazione.parquet"),
                     index=False)

    righe = []
    for s in semi:
        base = f"M5 GBM ancorato al mercato [BASE] s{s}"
        for altra in (f"selezione{len(tenute)}", f"pca{pc.shape[1]}"):
            nome = f"M5 GBM ancorato al mercato [{altra}] s{s}"
            r = paired_pairs(preds, [(nome, base)]).iloc[0]
            righe.append({"seme": s, "variante": altra,
                          "differenza": r["differenza"], "ic_basso": r["ic_basso"],
                          "ic_alto": r["ic_alto"]})
    tab = pd.DataFrame(righe)
    print("\n=== VALIDAZIONE: rappresentazione - BASE (negativo = meglio) ===")
    print(tab.round(5).to_string(index=False))
    tab.to_csv(experiments.percorso("riassunto_collinearita_validazione.csv"), index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Collinearita' di BASE, in validazione")
    ap.add_argument("--descrivi", action="store_true")
    ap.add_argument("--misura", action="store_true")
    args = ap.parse_args()
    experiments.proteggi_produzione()
    if args.misura:
        misura()
    elif args.descrivi:
        descrivi()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
