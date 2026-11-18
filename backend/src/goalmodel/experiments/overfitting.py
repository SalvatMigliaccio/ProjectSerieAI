"""
Sovradattamento di M4 e M5: quanto peggiora un modello fuori dal suo training.

LA DOMANDA. Un modello che sovradatta e' bravo sulle partite che ha visto e
peggiore su quelle che non ha visto. Si misura confrontando, blocco per blocco
del walk-forward, l'errore IN-SAMPLE (sul training appena usato) con quello
FUORI CAMPIONE (sulla giornata predetta subito dopo).

PERCHE' LO SCARTO GREZZO NON BASTA. Anche un modello senza nessun parametro
stimato ha uno scarto fra training e test: le giornate di test possono essere
piu' o meno difficili della media storica, e una stagione piu' equilibrata
alza l'RPS di tutti. Quello scarto e' difficolta', non sovradattamento.

IL CONTROLLO: IL MERCATO. M1 non stima niente — e' la quota B365 senza
margine — quindi non puo' sovradattare. Il suo scarto training/test, sulle
STESSE righe, misura esattamente la differenza di difficolta'. Quello che
resta dopo averlo sottratto e' sovradattamento:

    eccesso = (test_modello - train_modello) - (test_mercato - train_mercato)

Positivo = il modello perde fuori campione piu' di quanto perda il mercato,
cioe' ha imparato qualcosa che vale solo sul training. L'intervallo viene dal
bootstrap a cluster sulla giornata, lo stesso dei confronti appaiati.

DUE FINESTRE IN-SAMPLE
  tutto   l'intero training. Stima stabile, ma mescola stagioni lontane.
  coda    le ultime `CODA` partite del training, le piu' vicine al test nel
          tempo. Per M4/M5 sono anche quelle su cui l'arresto anticipato ha
          scelto il numero di alberi, prima del riaddestramento finale su
          tutto: dentro il modello finale sono in-sample a pieno titolo.

COSA NON E'. Non e' un test di blocco e non consuma un confronto: non si
confronta un modello con un altro per decidere cosa tenere, si misura una
proprieta' dei modelli gia' valutati. m resta 3.

Perimetro di default: la sola Serie A, cioe' le stesse 1140 partite su cui
sono stati pubblicati tutti i risultati. Con `--leghe big5` si usano i dati
di tutte le leghe, ma i numeri non sono piu' confrontabili con CLAUDE.md.

Scrive solo in `experiments/output/`.

Uso:
    python -m goalmodel.experiments.overfitting --lancia      # ~10-20 minuti
    python -m goalmodel.experiments.overfitting --analizza
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from .. import config, experiments
from ..evaluation.evaluate import (
    PROB_COLS,
    cluster_bootstrap,
    load_dataset,
    outcome_index,
    rps,
    walk_forward,
)
from ..models.baseline import MarketOnly, Model
from ..models.gbm import MarketAnchoredGBM, PoissonGBM

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("overfitting")

# Ampiezza della finestra "coda": una stagione di Serie A, ed e' anche
# l'`es_holdout` di default dei GBM.
CODA = 380

MERCATO = MarketOnly().name

FILE_PREVISIONI = "wf_overfitting_{perimetro}.parquet"
FILE_INSAMPLE = "overfitting_insample_{perimetro}.parquet"


def devianza_poisson(y: np.ndarray, mu: np.ndarray) -> np.ndarray:
    """Devianza Poisson per riga. E' la perdita che LightGBM minimizza davvero."""
    with np.errstate(divide="ignore", invalid="ignore"):
        termine = np.where(y > 0, y * np.log(y / mu), 0.0)
    return 2.0 * (termine - (y - mu))


def _metriche(pred: pd.DataFrame, righe: pd.DataFrame) -> tuple[float, float]:
    """RPS medio e devianza media (casa + trasferta) su un insieme di righe."""
    p = pred[PROB_COLS].to_numpy(dtype=float)
    r = rps(p, outcome_index(righe["FTR"])).mean()
    dev = (
        devianza_poisson(righe["FTHG"].to_numpy(float), pred["lambda_home"].to_numpy(float))
        + devianza_poisson(righe["FTAG"].to_numpy(float), pred["lambda_away"].to_numpy(float))
    ).mean()
    return float(r), float(dev)


class Diagnosi(Model):
    """
    Avvolge un modello e ne misura l'errore sul training appena usato.

    Non cambia niente del modello: `fit` e `predict` passano al modello
    interno, e il nome resta lo stesso, quindi `walk_forward` non sa di avere
    davanti un involucro (sostituzione di Liskov, come chiede CLAUDE.md).
    L'unico effetto collaterale e' una riga in `self.registro` per blocco.

    Il mercato viene predetto sulle STESSE righe del training, e solo sulle
    righe dove entrambi hanno una previsione: un confronto su insiemi diversi
    non sottrarrebbe la difficolta', la sposterebbe.
    """

    def __init__(self, interno: Model) -> None:
        self.interno = interno
        self.mercato = MarketOnly()
        self.registro: list[dict] = []

    @property
    def name(self) -> str:
        return self.interno.name

    def fit(self, train: pd.DataFrame) -> Diagnosi:
        self.interno.fit(train)
        giocate = train.dropna(subset=["FTHG", "FTAG", "FTR"]).sort_values("date")
        mod = self.interno.predict(giocate)
        mkt = self.mercato.predict(giocate)
        ok = (mod[PROB_COLS].notna().all(axis=1) & mkt[PROB_COLS].notna().all(axis=1)).to_numpy()

        riga: dict = {"modello": self.name, "n_train": len(giocate), "n_insample": int(ok.sum())}
        for finestra, maschera in (("tutto", ok), ("coda", ok & (np.arange(len(giocate)) >= len(giocate) - CODA))):
            righe = giocate[maschera]
            riga[f"rps_mod_{finestra}"], riga[f"dev_mod_{finestra}"] = _metriche(mod[maschera], righe)
            riga[f"rps_mkt_{finestra}"], riga[f"dev_mkt_{finestra}"] = _metriche(mkt[maschera], righe)

        alberi = getattr(self.interno, "best_iters_", [])
        riga["alberi_casa"] = alberi[0] if len(alberi) > 0 else np.nan
        riga["alberi_fuori"] = alberi[1] if len(alberi) > 1 else np.nan
        self.registro.append(riga)
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        # Il blocco si conosce solo qui: walk_forward chiama fit e poi predict
        # sullo stesso blocco, quindi l'ultima riga del registro e' la sua.
        self.registro[-1]["season"] = test["season"].iloc[0]
        self.registro[-1]["matchday"] = int(test["matchday"].min())
        return self.interno.predict(test)


def _dataset(perimetro: str) -> pd.DataFrame:
    df = load_dataset()
    if perimetro == "serie_a":
        df = df[df["league"].isin(config.LEAGUES_PRODUZIONE)].reset_index(drop=True)
    return df


def lancia(perimetro: str = "serie_a") -> None:
    """
    Walk-forward con M4 senza mercato, M4 con mercato, M5, piu' il mercato.

    Iperparametri quelli di produzione, seme 0: si misura il modello che e'
    stato valutato, non una variante.
    """
    experiments.proteggi_produzione()
    df = _dataset(perimetro)

    diagnosi = [
        Diagnosi(PoissonGBM(use_market=False, **config.GBM_PARAMS_NO_MARKET)),
        Diagnosi(PoissonGBM(use_market=True, **config.GBM_PARAMS_MARKET)),
        Diagnosi(MarketAnchoredGBM(**config.GBM_PARAMS_ANCHORED)),
    ]
    preds = walk_forward(df, [MarketOnly(), *diagnosi])
    preds.to_parquet(experiments.percorso(FILE_PREVISIONI.format(perimetro=perimetro)), index=False)

    insample = pd.DataFrame([r for d in diagnosi for r in d.registro])
    insample.to_parquet(experiments.percorso(FILE_INSAMPLE.format(perimetro=perimetro)), index=False)
    log.info("scritti %d blocchi x %d modelli", insample["matchday"].count() // len(diagnosi),
             len(diagnosi))


def _fuori_campione(preds: pd.DataFrame) -> pd.DataFrame:
    """RPS e devianza per partita, fuori campione, una riga per (partita, modello)."""
    ok = preds.dropna(subset=PROB_COLS).copy()
    ok["rps"] = rps(ok[PROB_COLS].to_numpy(float), outcome_index(ok["FTR"]))
    ok["dev"] = (devianza_poisson(ok["FTHG"].to_numpy(float), ok["lambda_home"].to_numpy(float))
                 + devianza_poisson(ok["FTAG"].to_numpy(float), ok["lambda_away"].to_numpy(float)))
    return ok


def eccesso(preds: pd.DataFrame, insample: pd.DataFrame, modello: str,
            metrica: str, finestra: str) -> dict:
    """
    Lo scarto training/test del modello oltre quello del mercato, con IC.

    Per partita: (test_mod - test_mkt) - (train_mod - train_mkt) del suo
    blocco. Il secondo termine e' costante dentro il blocco, quindi la media
    pesata per partita e il bootstrap sulla giornata si applicano tali e quali.
    """
    oos = _fuori_campione(preds)
    chiave = ["season", "matchday", "home_team", "away_team"]
    largo = oos.pivot_table(index=chiave, columns="model", values=metrica)
    largo = largo[[modello, MERCATO]].dropna()

    blocco = insample[insample["modello"] == modello].set_index(["season", "matchday"])
    ins = (blocco[f"{metrica}_mod_{finestra}"] - blocco[f"{metrica}_mkt_{finestra}"])
    # La giornata di un blocco e' la sua prima: con stride 1 coincide con
    # quella di ogni partita del blocco.
    idx = pd.MultiIndex.from_arrays([largo.index.get_level_values("season"),
                                     largo.index.get_level_values("matchday")])
    ins_per_partita = ins.reindex(idx).to_numpy(float)

    oos_diff = (largo[modello] - largo[MERCATO]).to_numpy(float)
    d = oos_diff - ins_per_partita
    cluster = (idx.get_level_values(0).astype(str) + "-"
               + idx.get_level_values(1).astype(str)).to_numpy()
    ok = np.isfinite(d)
    res = cluster_bootstrap(d[ok], cluster[ok])
    res.update({
        "modello": modello, "metrica": metrica, "finestra": finestra,
        "train_mod_meno_mkt": float(np.nanmean(ins_per_partita)),
        "test_mod_meno_mkt": float(oos_diff.mean()),
    })
    return res


def analizza(perimetro: str = "serie_a") -> None:
    preds = pd.read_parquet(experiments.percorso(FILE_PREVISIONI.format(perimetro=perimetro)))
    insample = pd.read_parquet(experiments.percorso(FILE_INSAMPLE.format(perimetro=perimetro)))
    modelli = [m for m in preds["model"].unique() if m != MERCATO]
    pd.set_option("display.width", 200)

    # 1. Livelli: quanto vale ciascuno dentro e fuori, accanto al mercato.
    oos = _fuori_campione(preds)
    livelli = []
    for m in modelli:
        b = insample[insample["modello"] == m]
        livelli.append({
            "modello": m,
            "RPS_train_tutto": b["rps_mod_tutto"].mean(),
            "RPS_train_coda": b["rps_mod_coda"].mean(),
            "RPS_test": oos.loc[oos["model"] == m, "rps"].mean(),
            "mkt_train_tutto": b["rps_mkt_tutto"].mean(),
            "mkt_train_coda": b["rps_mkt_coda"].mean(),
            "mkt_test": oos.loc[oos["model"] == MERCATO, "rps"].mean(),
        })
    livelli = pd.DataFrame(livelli).set_index("modello")
    print("\n=== 1. RPS dentro e fuori dal training (media sui blocchi) ===")
    print(livelli.round(5).to_string())

    # 2. L'eccesso, che e' la risposta.
    righe = [eccesso(preds, insample, m, metrica, finestra)
             for m in modelli for metrica in ("rps", "dev") for finestra in ("tutto", "coda")]
    tab = pd.DataFrame(righe)
    tab["verdetto"] = np.where(tab["ic_basso"] > 0, "SOVRADATTA",
                               np.where(tab["ic_alto"] < 0, "generalizza meglio", "nessun eccesso"))
    colonne = ["modello", "metrica", "finestra", "train_mod_meno_mkt", "test_mod_meno_mkt",
               "differenza", "ic_basso", "ic_alto", "verdetto"]
    print("\n=== 2. Eccesso di scarto train->test oltre il mercato (positivo = sovradatta) ===")
    print(tab[colonne].round(5).to_string(index=False))

    # 3. Per stagione: un sovradattamento che cresce nel tempo sarebbe deriva.
    per_stagione = []
    for m in modelli:
        for s in sorted(preds["season"].unique()):
            sub = preds[preds["season"] == s]
            r = eccesso(sub, insample[insample["season"] == s], m, "rps", "coda")
            per_stagione.append({"modello": m, "stagione": s, "eccesso": r["differenza"],
                                 "ic_basso": r["ic_basso"], "ic_alto": r["ic_alto"]})
    print("\n=== 3. Eccesso RPS (finestra coda) per stagione ===")
    print(pd.DataFrame(per_stagione).round(5).to_string(index=False))

    # 4. Alberi: la capacita' effettivamente usata.
    alberi = insample.groupby("modello")[["alberi_casa", "alberi_fuori"]].describe(
        percentiles=[0.5]).round(1)
    print("\n=== 4. Alberi scelti dall'arresto anticipato, per blocco ===")
    print(alberi.to_string())
    degeneri = insample.assign(
        uno=(insample["alberi_casa"] <= 1) & (insample["alberi_fuori"] <= 1)
    ).groupby("modello")["uno"].mean()
    print("\nquota di blocchi con un solo albero per lato (modello degenere):")
    print(degeneri.round(3).to_string())

    tab[colonne].to_csv(experiments.percorso(f"riassunto_overfitting_{perimetro}.csv"), index=False)
    log.info("scritto riassunto_overfitting_%s.csv", perimetro)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sovradattamento di M4/M5 contro il mercato")
    ap.add_argument("--lancia", action="store_true", help="walk-forward con diagnosi in-sample")
    ap.add_argument("--analizza", action="store_true", help="tabelle dai file gia' prodotti")
    ap.add_argument("--leghe", choices=["serie_a", "big5"], default="serie_a")
    args = ap.parse_args()
    experiments.proteggi_produzione()
    if args.lancia:
        lancia(args.leghe)
    if args.analizza:
        analizza(args.leghe)
    if not (args.lancia or args.analizza):
        ap.print_help()


if __name__ == "__main__":
    main()
