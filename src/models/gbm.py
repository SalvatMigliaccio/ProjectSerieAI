"""
M4: LightGBM con obiettivo Poisson, due modelli di gol separati.

LA DOMANDA A CUI SERVE RISPONDERE
Non "il GBM batte il mercato", che e' improbabile, ma: **xG, PPDA e forma
recente contengono informazione che il mercato non ha gia' incorporato?**
Per questo il modello esiste in due varianti, e la piu' importante e' la prima.

  senza mercato  Solo le feature di forma. Se questa variante arriva vicino al
                 mercato, vuol dire che i dati di gioco ricostruiscono da soli
                 buona parte di cio' che il mercato sa. Se resta molto
                 indietro, il mercato sa cose che nei dati non ci sono
                 (infortuni, formazioni, motivazioni) e la strada e' partire
                 dalle quote.
  con mercato    Tutto insieme. La differenza rispetto al mercato da solo
                 misura quanto si guadagna correggendolo, che e' il vero caso
                 d'uso in produzione.

DUE MODELLI, NON UNO
Si stimano separatamente i gol in casa e quelli in trasferta, entrambi con
obiettivo Poisson. Non e' la stessa cosa che stimare un modello unico in
formato lungo: qui il modello dei gol casalinghi puo' usare in modo asimmetrico
le feature di casa e di trasferta, e il vantaggio del campo non e' un
coefficiente unico ma una differenza fra due funzioni.

OBIETTIVO POISSON E NON REGRESSIONE QUADRATICA
Il conteggio dei gol e' non negativo e a varianza crescente con la media.
L'obiettivo Poisson ottimizza la devianza giusta e garantisce lambda > 0, che
serve perche' quel lambda finisce dentro un'esponenziale nella matrice dei
risultati.

IPERPARAMETRI
Il numero di alberi si tara sulla validazione, come la half-life di M3. Il
resto e' fissato a valori prudenti e non tarato: con 114 riaddestramenti e
~3500 righe per fit, alberi piccoli e molto attrito sono la scelta difendibile.

Uso:
    python -m src.models.gbm --tune
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from .. import config
from ..features.market import FEATURES_T24
from .baseline import Model, predictions_from_lambdas

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("gbm")

# Colonne che non sono mai feature: chiavi, esiti, diagnostica di mercato.
NEVER_FEATURES = {
    "league", "season", "home_team", "away_team", "date", "matchday",
    "FTHG", "FTAG", "FTR", "is_burn_in",
}

# Le feature di mercato utilizzabili a T-24h, senza le due colonne testuali
# che dicono da quale book arriva la quota.
MARKET_FEATURES = [c for c in FEATURES_T24 if not c.endswith("_source")]


def form_features(df: pd.DataFrame) -> list[str]:
    """Le colonne prodotte da features/form.py: medie mobili e contatori."""
    return [
        c for c in df.columns
        if c not in NEVER_FEATURES
        and not c.startswith(("mkt_", "close_", "drift_"))
        and pd.api.types.is_numeric_dtype(df[c])
    ]


class PoissonGBM(Model):
    """
    M4. `use_market` decide quale delle due domande si sta facendo.

    Le squadre non entrano come identita': non ci sono dummy di squadra. Tutta
    la forza di una squadra deve arrivare dalle sue medie mobili. E' una
    limitazione voluta — l'identita' la modellano gia' M2 e M3 — e rende il
    confronto interpretabile: se M4 senza mercato perde contro M3, vuol dire
    che la forza stimata dai risultati batte le medie mobili delle statistiche.
    """

    def __init__(
        self,
        use_market: bool,
        n_estimators: int = 300,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        min_child_samples: int = 50,
        rho: float = config.DC_RHO,
        seed: int = 0,
    ) -> None:
        self.use_market = use_market
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.rho = rho
        self.seed = seed
        self.features_: list[str] = []
        self.model_home_: LGBMRegressor | None = None
        self.model_away_: LGBMRegressor | None = None

    @property
    def name(self) -> str:
        return f"M4 GBM {'con' if self.use_market else 'senza'} mercato"

    def _make(self) -> LGBMRegressor:
        return LGBMRegressor(
            objective="poisson",
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=self.min_child_samples,
            colsample_bytree=0.8,
            subsample=0.8,
            subsample_freq=1,
            reg_lambda=1.0,
            random_state=self.seed,
            n_jobs=1,
            verbose=-1,
        )

    def fit(self, train: pd.DataFrame) -> "PoissonGBM":
        df = train.dropna(subset=["FTHG", "FTAG"])
        self.features_ = form_features(df)
        if self.use_market:
            self.features_ = self.features_ + [c for c in MARKET_FEATURES if c in df.columns]

        if df.empty or not self.features_:
            self.model_home_ = self.model_away_ = None
            return self

        x = df[self.features_]
        self.model_home_ = self._make().fit(x, df["FTHG"].to_numpy(dtype=float))
        self.model_away_ = self._make().fit(x, df["FTAG"].to_numpy(dtype=float))
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        if self.model_home_ is None:
            return self._empty(test)
        x = test[self.features_]
        # LightGBM con obiettivo Poisson restituisce gia' la media, non il
        # logaritmo. Si taglia comunque sopra zero: un lambda nullo mandera'
        # la matrice dei risultati in una divisione per zero.
        lam_h = np.clip(self.model_home_.predict(x), 1e-4, None)
        lam_a = np.clip(self.model_away_.predict(x), 1e-4, None)
        return predictions_from_lambdas(lam_h, lam_a, index=test.index, rho=self.rho)


# ---------------------------------------------------------------------------

def tune_trees(
    df: pd.DataFrame,
    grid: tuple[int, ...] = (25, 50, 75, 100, 150, 300),
    validation_seasons: list[str] | None = None,
) -> pd.DataFrame:
    """
    Numero di alberi scelto sulla validazione, per ciascuna variante.

    La griglia parte da 25 e non da 150 perche' la prima versione partiva di
    li' e l'RPS cresceva in modo monotono con gli alberi: il minimo stava
    fuori dalla griglia, sotto. Con ~3500 righe e 50-70 feature il modello
    sovradatta molto presto, e la scelta giusta e' un modello piccolo.
    """
    from ..evaluate import PROB_COLS, outcome_index, rps, walk_forward

    validation_seasons = validation_seasons or config.VALIDATION_SEASONS
    rows = []
    for use_market in (False, True):
        for n in grid:
            model = PoissonGBM(use_market=use_market, n_estimators=n)
            preds = walk_forward(df, [model], test_seasons=validation_seasons)
            ok = preds.dropna(subset=PROB_COLS)
            score = rps(ok[PROB_COLS].to_numpy(dtype=float), outcome_index(ok["FTR"])).mean()
            rows.append({
                "mercato": use_market,
                "alberi": n,
                "RPS_validazione": float(score),
            })
            log.info("mercato=%s alberi=%4d -> RPS validazione %.6f", use_market, n, score)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="LightGBM Poisson a due gol")
    ap.add_argument("--tune", action="store_true", help="tara il numero di alberi sulla validazione")
    args = ap.parse_args()

    if args.tune:
        from ..evaluate import load_dataset
        tab = tune_trees(load_dataset())
        print(tab.to_string(index=False))
        best = tab.loc[tab.groupby("mercato")["RPS_validazione"].idxmin()]
        print("\nscelte:")
        print(best.to_string(index=False))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
