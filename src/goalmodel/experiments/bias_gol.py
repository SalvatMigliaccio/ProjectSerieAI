"""
Bias sui gol: i lambda previsti contro i gol segnati, per sottogruppi.

LA DOMANDA. L'1X2 e' un derivato; il target del progetto sono i gol. Un
modello puo' avere un buon RPS e sbagliare sistematicamente il LIVELLO dei
gol in una parte del campo — troppi gol attesi nelle partite aperte, troppo
pochi alle sfavorite — e quell'errore passa nei mercati derivati (over/under,
gol-gol, risultato esatto) che l'RPS non guarda.

COSA SI MISURA, sulle previsioni FUORI CAMPIONE del walk-forward:
  1. livello      gol segnati - lambda, per lato e in totale;
  2. pendenza     regressione Poisson dei gol su log(lambda): pendenza 1 =
                  calibrato; < 1 = il modello separa troppo (lambda alti
                  troppo alti, bassi troppo bassi); > 1 = separa troppo poco;
  3. sottogruppi  per fascia di favorito, di gol attesi, per stagione, per
                  squadra (con Napoli in evidenza: e' il caso d'uso);
  4. derivati     pareggi, over 2.5 e gol-gol: probabilita' media contro
                  frequenza. I pareggi contano a parte: due Poisson
                  indipendenti (`config.DC_RHO = 0`) sono noti per
                  sottostimarli.

DIAGNOSTICA, NON SEGNALE. Sono decine di viste sugli stessi dati, e qualcuna
uscira' "significativa" per caso: con 20 squadre a 0.05, una in media. Vale la
stessa regola scritta in CLAUDE.md per la distorsione del mercato: un residuo
trovato qui si spiega con il modello, NON diventa una feature e non corregge
la produzione. Per le squadre si riporta anche la soglia di Bonferroni.

INTERVALLI. Bootstrap a cluster sulla giornata, come ogni altro confronto: le
dieci partite di una giornata sono predette dallo stesso addestramento.

Legge `experiments/output/wf_overfitting_<perimetro>.parquet`, prodotto da
`goalmodel.experiments.overfitting --lancia`. Non riaddestra niente.

Uso:
    python -m goalmodel.experiments.bias_gol
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy import stats

from .. import config, experiments
from ..evaluation.evaluate import cluster_bootstrap
from .overfitting import FILE_PREVISIONI, MERCATO

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("bias_gol")

SQUADRA_IN_EVIDENZA = "Napoli"


def _cluster(df: pd.DataFrame) -> np.ndarray:
    return (df["season"].astype(str) + "-" + df["matchday"].astype(str)).to_numpy()


def _ic(diff: np.ndarray, cluster: np.ndarray) -> dict:
    ok = np.isfinite(diff)
    r = cluster_bootstrap(diff[ok], cluster[ok])
    return {"scarto": r["differenza"], "ic_basso": r["ic_basso"], "ic_alto": r["ic_alto"],
            "n": int(ok.sum())}


def _lungo(df: pd.DataFrame) -> pd.DataFrame:
    """Una riga per (partita, lato): i gol di ogni squadra con il suo lambda."""
    casa = df.assign(lato="casa", squadra=df["home_team"], gol=df["FTHG"], lam=df["lambda_home"])
    fuori = df.assign(lato="fuori", squadra=df["away_team"], gol=df["FTAG"], lam=df["lambda_away"])
    return pd.concat([casa, fuori], ignore_index=True)


def pendenza(gol: np.ndarray, lam: np.ndarray) -> tuple[float, float, float]:
    """
    Regressione Poisson log E[gol] = a + b log(lambda), con errore standard.

    Newton-Raphson a mano su due parametri: statsmodels non e' una dipendenza
    del progetto, e due parametri non la giustificano. L'errore standard e'
    quello del modello (non robusto ai cluster): serve a leggere l'ordine di
    grandezza, la decisione si prende sugli intervalli a cluster sopra.
    """
    x = np.column_stack([np.ones_like(lam), np.log(lam)])
    beta = np.array([0.0, 1.0])
    for _ in range(50):
        mu = np.exp(x @ beta)
        grad = x.T @ (gol - mu)
        hess = x.T @ (x * mu[:, None])
        passo = np.linalg.solve(hess, grad)
        beta = beta + passo
        if np.abs(passo).max() < 1e-10:
            break
    se = np.sqrt(np.diag(np.linalg.inv(hess)))
    return float(beta[0]), float(beta[1]), float(se[1])


def livello(preds: pd.DataFrame) -> pd.DataFrame:
    righe = []
    for m, g in preds.groupby("model", sort=False):
        cl = _cluster(g)
        for nome, gol, lam in (
            ("casa", g["FTHG"], g["lambda_home"]),
            ("fuori", g["FTAG"], g["lambda_away"]),
            ("totale", g["FTHG"] + g["FTAG"], g["lambda_home"] + g["lambda_away"]),
        ):
            r = _ic((gol - lam).to_numpy(float), cl)
            righe.append({"modello": m, "gol": nome, "previsti": lam.mean(),
                          "segnati": gol.mean(), **r})
    return pd.DataFrame(righe)


def pendenze(preds: pd.DataFrame) -> pd.DataFrame:
    righe = []
    for m, g in preds.groupby("model", sort=False):
        lungo = _lungo(g)
        a, b, se = pendenza(lungo["gol"].to_numpy(float), lungo["lam"].to_numpy(float))
        righe.append({"modello": m, "intercetta": a, "pendenza": b, "se": se,
                      "z_da_1": (b - 1) / se})
    return pd.DataFrame(righe)


def per_gruppo(preds: pd.DataFrame, gruppo: pd.Series, nome: str) -> pd.DataFrame:
    """Scarto sui gol totali dentro ciascun gruppo, per modello."""
    righe = []
    for m, g in preds.groupby("model", sort=False):
        etichette = gruppo.loc[g.index]
        for valore in etichette.dropna().unique():
            sub = g[etichette == valore]
            diff = ((sub["FTHG"] + sub["FTAG"]) - (sub["lambda_home"] + sub["lambda_away"]))
            righe.append({"modello": m, nome: valore,
                          **_ic(diff.to_numpy(float), _cluster(sub))})
    return pd.DataFrame(righe).sort_values(["modello", nome])


def derivati(preds: pd.DataFrame) -> pd.DataFrame:
    """Pareggi, over 2.5, gol-gol: probabilita' media contro frequenza osservata."""
    tot = preds["FTHG"] + preds["FTAG"]
    eventi = {
        "pareggio": ("p_draw", preds["FTR"] == "D"),
        "over 2.5": ("p_over25", tot > 2.5),
        "gol-gol": ("p_btts", (preds["FTHG"] > 0) & (preds["FTAG"] > 0)),
    }
    righe = []
    for m, g in preds.groupby("model", sort=False):
        for nome, (col, accaduto) in eventi.items():
            diff = accaduto.loc[g.index].astype(float) - g[col]
            righe.append({"modello": m, "evento": nome, "prevista": g[col].mean(),
                          "osservata": accaduto.loc[g.index].mean(),
                          **_ic(diff.to_numpy(float), _cluster(g))})
    return pd.DataFrame(righe)


def per_squadra(preds: pd.DataFrame, modello: str) -> pd.DataFrame:
    """
    Gol segnati e subiti da ogni squadra contro quelli previsti.

    Un test per squadra e per verso: la soglia di Bonferroni si riporta
    accanto, perche' su 2 x ~25 squadre qualcuna esce per caso.
    """
    g = preds[preds["model"] == modello]
    righe = []
    for squadra in sorted(set(g["home_team"]) | set(g["away_team"])):
        casa, fuori = g[g["home_team"] == squadra], g[g["away_team"] == squadra]
        fatti = np.concatenate([(casa["FTHG"] - casa["lambda_home"]).to_numpy(float),
                                (fuori["FTAG"] - fuori["lambda_away"]).to_numpy(float)])
        subiti = np.concatenate([(casa["FTAG"] - casa["lambda_away"]).to_numpy(float),
                                 (fuori["FTHG"] - fuori["lambda_home"]).to_numpy(float)])
        cl = np.concatenate([_cluster(casa), _cluster(fuori)])
        if len(fatti) < 19:  # meno di mezza stagione: troppo poco per dire niente
            continue
        f, s = _ic(fatti, cl), _ic(subiti, cl)
        righe.append({"squadra": squadra, "partite": len(fatti),
                      "fatti_scarto": f["scarto"], "fatti_ic": f"[{f['ic_basso']:+.3f}, {f['ic_alto']:+.3f}]",
                      "subiti_scarto": s["scarto"], "subiti_ic": f"[{s['ic_basso']:+.3f}, {s['ic_alto']:+.3f}]",
                      "fatti_p": 2 * stats.norm.sf(abs(f["scarto"]) / max(np.std(fatti, ddof=1) / np.sqrt(len(fatti)), 1e-12)),
                      "subiti_p": 2 * stats.norm.sf(abs(s["scarto"]) / max(np.std(subiti, ddof=1) / np.sqrt(len(subiti)), 1e-12))})
    return pd.DataFrame(righe).sort_values("fatti_scarto")


def analizza(perimetro: str = "serie_a") -> None:
    experiments.proteggi_produzione()
    preds = pd.read_parquet(experiments.percorso(FILE_PREVISIONI.format(perimetro=perimetro)))
    preds = preds.dropna(subset=["lambda_home", "lambda_away", "FTHG", "FTAG"]).reset_index(drop=True)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)

    print("\n=== 1. Livello: gol segnati - previsti (positivo = il modello ne prevede troppo pochi) ===")
    print(livello(preds).round(4).to_string(index=False))

    print("\n=== 2. Pendenza: log E[gol] = a + b log(lambda)   (b=1 calibrato, b<1 separa troppo) ===")
    print(pendenze(preds).round(4).to_string(index=False))

    # Gruppi definiti dal MERCATO, uguali per tutti i modelli: confrontare
    # modelli su fasce definite da ciascuno mescolerebbe partite diverse.
    mkt = preds[preds["model"] == MERCATO].set_index(["season", "home_team", "away_team"])
    chiave = pd.MultiIndex.from_frame(preds[["season", "home_team", "away_team"]])
    supremazia = (mkt["p_home"] - mkt["p_away"]).reindex(chiave).to_numpy()
    attesi = (mkt["lambda_home"] + mkt["lambda_away"]).reindex(chiave).to_numpy()

    fascia_fav = pd.Series(pd.cut(supremazia, [-1, -0.25, 0.0, 0.25, 0.5, 1],
                                  labels=["1 ospite favorito", "2 ospite leggero",
                                          "3 casa leggera", "4 casa favorita",
                                          "5 casa netta"]), index=preds.index)
    print("\n=== 3a. Gol totali per fascia di favorito (secondo il mercato) ===")
    print(per_gruppo(preds, fascia_fav, "fascia").round(4).to_string(index=False))

    fascia_gol = pd.Series(pd.qcut(attesi, 5, labels=[f"q{i}" for i in range(1, 6)]),
                           index=preds.index)
    print("\n=== 3b. Gol totali per quintile di gol attesi (secondo il mercato) ===")
    print(per_gruppo(preds, fascia_gol, "quintile").round(4).to_string(index=False))

    print("\n=== 3c. Gol totali per stagione ===")
    print(per_gruppo(preds, preds["season"], "stagione").round(4).to_string(index=False))

    print("\n=== 4. Eventi derivati: frequenza osservata - probabilita' prevista ===")
    print(derivati(preds).round(4).to_string(index=False))

    squadre = per_squadra(preds, MERCATO)
    soglia = 0.05 / (2 * len(squadre))
    print(f"\n=== 5. Per squadra, M1 (scarto per partita; Bonferroni a {soglia:.4f}) ===")
    print(squadre.round(4).to_string(index=False))
    sopra = squadre[(squadre["fatti_p"] < soglia) | (squadre["subiti_p"] < soglia)]
    print(f"\nsquadre oltre Bonferroni: {', '.join(sopra['squadra']) or 'nessuna'}")
    if SQUADRA_IN_EVIDENZA in set(squadre["squadra"]):
        print(f"{SQUADRA_IN_EVIDENZA}:")
        print(squadre[squadre["squadra"] == SQUADRA_IN_EVIDENZA].round(4).to_string(index=False))

    livello(preds).to_csv(experiments.percorso(f"riassunto_bias_gol_{perimetro}.csv"), index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Bias sui gol, fuori campione")
    ap.add_argument("--leghe", choices=["serie_a", "big5"], default="serie_a")
    args = ap.parse_args()
    analizza(args.leghe)


if __name__ == "__main__":
    main()
