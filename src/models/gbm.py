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

CAPACITA': PERCHE' IL NUMERO DI ALBERI NON STA NELLA GRIGLIA
La prima versione metteva `n_estimators` nella griglia e il minimo cadeva sul
bordo inferiore, con l'RPS che cresceva in modo monotono al crescere degli
alberi. Non era una griglia troppo stretta: era sovradattamento immediato.
Abbassare ancora il bordo lo avrebbe solo spostato. La risposta giusta e'
togliere capacita' dove serve — passo di apprendimento piccolo, alberi da 4-8
foglie, foglie con almeno 50 osservazioni, campionamento di righe e colonne,
penalita' L2 esplicita — e lasciare che il numero di alberi lo decida
l'arresto anticipato, con un tetto alto che non deve mai essere raggiunto.

L'ARRESTO ANTICIPATO NON GUARDA IL TEST
Il gruppo di controllo per l'arresto si ritaglia dalla CODA DEL TRAINING: le
ultime `es_holdout` partite prima del taglio. E' informazione gia' disponibile
al momento della previsione, quindi non e' leakage. Trovato il numero di alberi,
si riaddestra su tutto il training con quel numero: altrimenti si butterebbe
via proprio la parte piu' recente, che e' la piu' informativa.

Uso:
    python -m src.models.gbm --tune          # ricerca iperparametri su validazione
    python -m src.models.gbm --tune --stride 1   # piu' lenta e piu' fedele
"""

from __future__ import annotations

import argparse
import itertools
import logging

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation

from .. import config
from ..features.market import FEATURES_T24
from .baseline import Model, predictions_from_lambdas

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("gbm")

# Colonne che non sono mai feature: chiavi, esiti, diagnostica di mercato.
NEVER_FEATURES = {
    "league", "season", "home_team", "away_team", "date", "matchday", "_block",
    "FTHG", "FTAG", "FTR", "is_burn_in", "n_train",
}

# Le feature di mercato utilizzabili a T-24h, senza le due colonne testuali
# che dicono da quale book arriva la quota.
MARKET_FEATURES = [c for c in FEATURES_T24 if not c.endswith("_source")]

# BLOCCHI MISURATI E SCARTATI. Restano nel dataset — servono a rimisurarli, per
# esempio su un perimetro piu' largo — ma NON entrano nel modello.
#
# Tenerli "male che vada non fanno danno" sarebbe sbagliato: con ~3400 righe di
# training la diluizione e' reale e gia' osservata (M4 con il mercato fra
# cinquanta feature perde contro M5 ancorato di -0.0023, con intervallo netto,
# proprio perche' le quote si diluivano).
#
# Blocco A, contesto. Misurato DUE volte, e la seconda e' quella che vale.
#
#   senza coppe (8 set 2026)   +0.00008   IC [-0.00007, +0.00024]
#   CON coppe   (8 set 2026)   -0.00004   IC [-0.00014, +0.00007]
#
# La prima era monca: riposo e congestione di solo campionato sono quasi
# uguali per tutti, perche' le date delle giornate non cambiano quando una
# squadra gioca in Europa. La seconda include Champions, Europa e Conference
# (`ingest --stage cups`), che sono il meccanismo vero — il 23.6% delle partite
# ha una coppa nei 14 giorni precedenti.
#
# Il verdetto non cambia e l'intervallo si stringe (semiampiezza 0.00010,
# minimo rilevabile 0.00015 contro i 0.00060 attesi da uno shift di 0.10 gol).
# Le colonne di coppa non ricevono quasi nessuno split — ranghi 61, 63 e 64 su
# 64 — e `diff_rest_days` SCENDE dal 5o al 28o posto quando il meccanismo vero
# entra: la sua importanza di prima era struttura spuria, non segnale.
BLOCCHI_SCARTATI: frozenset[str] = frozenset({
    "home_rest_days", "away_rest_days", "diff_rest_days",
    "home_matches_14d", "away_matches_14d", "diff_matches_14d",
    "home_cup_14d", "away_cup_14d", "diff_cup_14d",
    "is_midweek", "is_derby", "derby_intensity",
})

# BLOCCHI COSTRUITI MA NON ANCORA MISURATI. Fuori dal modello esattamente come
# quelli scartati, e per la stessa ragione: la regola del piano e' che un
# blocco entra solo se il suo intervallo appaiato sta sotto zero, e finche' la
# misura non c'e' non puo' entrare. Tenerli dentro "intanto" significherebbe
# diluire 52 feature con altre nove non validate, e falsare la misura del
# blocco successivo — che partirebbe da una base diversa da quella dichiarata.
#
# Appena un blocco e' misurato, la sua voce si sposta: in BLOCCHI_SCARTATI se
# l'intervallo contiene lo zero, via da entrambi gli insiemi se sta sotto.
BLOCCHI_NON_MISURATI: frozenset[str] = frozenset({
    "home_quota_minuti_assenti", "away_quota_minuti_assenti",
    "diff_quota_minuti_assenti",
    "home_quota_ga_assente", "away_quota_ga_assente",
    "diff_quota_ga_assente",
    "home_n_assenti", "away_n_assenti", "diff_n_assenti",
})

# Cio' che il modello di produzione non vede.
FUORI_DAL_MODELLO: frozenset[str] = BLOCCHI_SCARTATI | BLOCCHI_NON_MISURATI

# Griglia degli iperparametri. Il numero di alberi NON c'e': lo decide
# l'arresto anticipato. Si esplora a caso invece che esaustivamente perche'
# 144 combinazioni per due varianti non sono sostenibili, e su sei dimensioni
# la ricerca casuale copre meglio di una griglia grossolana a parita' di budget.
SEARCH_SPACE: dict[str, list] = {
    "learning_rate": [0.01, 0.02, 0.03],
    "num_leaves": [4, 6, 8],
    "min_child_samples": [50, 75, 100],
    "colsample_bytree": [0.6, 0.7, 0.8],
    "subsample": [0.6, 0.7, 0.8],
    "reg_lambda": [1.0, 5.0, 20.0],
}

# Spazio esteso per la variante ancorata. Con lo spazio comune TUTTE le prime
# cinque configurazioni sceglievano learning_rate al minimo e reg_lambda al
# massimo: un segnale sistematico, non il rumore di bordo che si vedeva su M4.
# Ha senso: M5 stima un residuo, e un residuo si sovradatta piu' facilmente di
# un livello. Si estendono quei due soli parametri, nella direzione indicata.
# Contava farlo: un M5 sotto-regolarizzato perderebbe contro il mercato per il
# motivo sbagliato, e il test decisivo non deciderebbe niente.
ANCHORED_SEARCH_SPACE: dict[str, list] = {
    **SEARCH_SPACE,
    "learning_rate": [0.0025, 0.005, 0.01],
    "reg_lambda": [20.0, 50.0, 150.0],
}


def form_features(df: pd.DataFrame,
                  escludi: tuple[str, ...] | None = None) -> list[str]:
    """
    Le colonne non di mercato: medie mobili, contatori, contesto.

    `escludi` serve a misurare un blocco di feature isolandolo. Si toglie dal
    MODELLO, non dal dataset: cosi' le due varianti girano sulle stesse righe
    e il confronto resta appaiato, che e' l'unico modo di avere un intervallo
    stretto abbastanza da decidere.

    `None` (il default) significa "escludi tutto cio' che non e' ancora
    entrato nel modello": i blocchi misurati e scartati e quelli costruiti ma
    non ancora misurati. Passare `()` li rimette dentro, ed e' quello che fa
    `evaluate.misura_blocco` per poterli misurare.
    """
    fuori = set(NEVER_FEATURES) | set(FUORI_DAL_MODELLO if escludi is None else escludi)
    return [
        c for c in df.columns
        if c not in fuori
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
        learning_rate: float = 0.02,
        num_leaves: int = 6,
        min_child_samples: int = 75,
        colsample_bytree: float = 0.7,
        subsample: float = 0.7,
        reg_lambda: float = 5.0,
        max_trees: int = 3000,
        early_stopping_rounds: int = 100,
        es_holdout: int = 380,
        rho: float = config.DC_RHO,
        seed: int = 0,
        escludi: tuple[str, ...] | None = None,
    ) -> None:
        self.use_market = use_market
        # Le colonne da NON dare al modello. None = i blocchi gia' scartati;
        # una tupla (anche vuota) sovrascrive, ed e' come si misura un blocco.
        self.escludi = escludi if escludi is None else tuple(escludi)
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.colsample_bytree = colsample_bytree
        self.subsample = subsample
        self.reg_lambda = reg_lambda
        self.max_trees = max_trees
        self.early_stopping_rounds = early_stopping_rounds
        self.es_holdout = es_holdout
        self.rho = rho
        self.seed = seed
        self.features_: list[str] = []
        self.model_home_: LGBMRegressor | None = None
        self.model_away_: LGBMRegressor | None = None
        # Alberi effettivamente scelti dall'arresto anticipato, per lato.
        # Se si avvicinano a max_trees il tetto e' troppo basso e va alzato.
        self.best_iters_: list[int] = []

    # Sovrascritto da M5: dice se il modello parte da un init_score esterno.
    anchored = False

    # Etichetta che distingue una variante ridotta nel frame del
    # walk-forward, che indicizza per nome: senza, due varianti si
    # sovrascriverebbero a vicenda senza dare errore.
    suffisso: str = ""

    @property
    def name(self) -> str:
        return (f"M4 GBM {'con' if self.use_market else 'senza'} mercato"
                f"{self.suffisso}")

    def _params(self, n_estimators: int) -> dict:
        return dict(
            objective="poisson",
            n_estimators=n_estimators,
            # Con un init_score fornito da fuori, partire dalla media dei dati
            # sarebbe un doppio conteggio dell'intercetta.
            boost_from_average=not self.anchored,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=self.min_child_samples,
            colsample_bytree=self.colsample_bytree,
            subsample=self.subsample,
            subsample_freq=1,
            reg_lambda=self.reg_lambda,
            random_state=self.seed,
            n_jobs=1,
            verbose=-1,
        )

    def _fit_one(
        self,
        x: pd.DataFrame,
        y: np.ndarray,
        n_head: int,
        init: np.ndarray | None = None,
    ) -> LGBMRegressor:
        """
        Un lato solo: arresto anticipato sulla coda del training, poi
        riaddestramento su tutto con il numero di alberi trovato.

        `init` e' l'offset iniziale in scala logaritmica (l'init_score di
        LightGBM). Se c'e', il modello non stima i gol: stima lo SCARTO dai
        gol gia' previsti da qualcun altro. Lo usa M5 per partire dal mercato.
        """
        extra = {} if init is None else {
            "init_score": init[:n_head],
            "eval_init_score": [init[n_head:]],
        }
        probe = LGBMRegressor(**self._params(self.max_trees))
        probe.fit(
            x.iloc[:n_head], y[:n_head],
            eval_X=x.iloc[n_head:], eval_y=y[n_head:],
            eval_metric="poisson",
            callbacks=[
                early_stopping(self.early_stopping_rounds, verbose=False),
                log_evaluation(0),
            ],
            **extra,
        )
        best = probe.best_iteration_ or self.max_trees
        self.best_iters_.append(int(best))
        final = LGBMRegressor(**self._params(best))
        if init is None:
            return final.fit(x, y)
        return final.fit(x, y, init_score=init)

    def fit(self, train: pd.DataFrame) -> "PoissonGBM":
        df = train.dropna(subset=["FTHG", "FTAG"]).sort_values("date")
        self.features_ = form_features(df, self.escludi)
        if self.use_market:
            self.features_ = self.features_ + [c for c in MARKET_FEATURES if c in df.columns]

        n_head = len(df) - self.es_holdout
        if df.empty or not self.features_ or n_head < 200:
            self.model_home_ = self.model_away_ = None
            return self

        x = df[self.features_]
        self.best_iters_ = []
        self.model_home_ = self._fit_one(x, df["FTHG"].to_numpy(dtype=float), n_head)
        self.model_away_ = self._fit_one(x, df["FTAG"].to_numpy(dtype=float), n_head)
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


MKT_LAMBDA = ("mkt_lambda_home", "mkt_lambda_away")


class MarketAnchoredGBM(PoissonGBM):
    """
    M5: il GBM non stima i gol, stima lo SCARTO dai gol previsti dal mercato.

    PERCHE' NON BASTA DARE LE QUOTE COME FEATURE (che e' M4 con mercato)
    Fra cinquanta colonne, le quote sono una delle cinquanta. Il modello deve
    prima imparare a ricostruire il mercato dai suoi ingressi — spendendo
    alberi, e sbagliando — e solo dopo puo' correggerlo. Qui invece il mercato
    e' il punto di partenza esatto, dato come init_score in scala logaritmica,
    e ogni albero puo' occuparsi solo del residuo. E' lo stesso test di prima,
    ma con tutta la capacita' del modello puntata sulla domanda giusta.

    LA PROPRIETA' CHE RENDE IL TEST DECISIVO
    Se nelle feature di forma non c'e' informazione che il mercato non abbia
    gia', il modello non trova nulla da correggere, l'arresto anticipato ferma
    subito il bosco e M5 **degenera nel mercato**. Quindi:
      - M5 significativamente migliore del mercato -> il segnale c'e';
      - M5 indistinguibile dal mercato -> non c'e', e la domanda e' chiusa;
      - M5 significativamente PEGGIORE -> il modello sta aggiungendo rumore,
        segno che la regolarizzazione e' troppo debole, non che il segnale
        manchi. In quel caso il test non e' valido e va rifatto.
    L'ultimo caso e' il motivo per cui la regolarizzazione qui e' piu' forte
    che in M4: un residuo e' piu' facile da sovradattare di un livello.

    Le feature sono SOLO quelle non di mercato: darle di nuovo insieme
    all'ancoraggio significherebbe chiedere al modello di correggere il
    mercato usando il mercato.
    """

    anchored = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("use_market", False)
        super().__init__(**kwargs)

    @property
    def name(self) -> str:
        return f"M5 GBM ancorato al mercato{self.suffisso}"

    def fit(self, train: pd.DataFrame) -> "MarketAnchoredGBM":
        df = train.dropna(subset=["FTHG", "FTAG", *MKT_LAMBDA]).sort_values("date")
        self.features_ = form_features(df, self.escludi)

        n_head = len(df) - self.es_holdout
        if df.empty or not self.features_ or n_head < 200:
            self.model_home_ = self.model_away_ = None
            return self

        x = df[self.features_]
        self.best_iters_ = []
        for side, (col, goals) in enumerate(zip(MKT_LAMBDA, ("FTHG", "FTAG"))):
            init = np.log(df[col].to_numpy(dtype=float))
            model = self._fit_one(x, df[goals].to_numpy(dtype=float), n_head, init=init)
            if side == 0:
                self.model_home_ = model
            else:
                self.model_away_ = model
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        if self.model_home_ is None:
            return self._empty(test)
        x = test[self.features_]
        out = self._empty(test)
        ok = test[list(MKT_LAMBDA)].notna().all(axis=1).to_numpy()
        if not ok.any():
            return out

        lam = {}
        for col, model in zip(MKT_LAMBDA, (self.model_home_, self.model_away_)):
            # predict() restituisce exp(somma degli alberi) e NON include
            # l'init_score: va riaggiunto a mano in scala logaritmica.
            # Verificato: predict(X) == exp(predict(X, raw_score=True)).
            raw = model.predict(x[ok], raw_score=True)
            base = np.log(test.loc[ok, col].to_numpy(dtype=float))
            lam[col] = np.clip(np.exp(base + raw), 1e-4, None)

        filled = predictions_from_lambdas(
            lam[MKT_LAMBDA[0]], lam[MKT_LAMBDA[1]],
            index=test.index[ok], rho=self.rho,
        )
        out.loc[filled.index, filled.columns] = filled
        return out


class LogBlend(Model):
    """
    M6: media geometrica fra i lambda del mercato e quelli di M4 senza mercato.

        log lambda = w * log(lambda_GBM) + (1 - w) * log(lambda_mercato)

    E' la versione povera dello stesso test di M5, e serve da controllo. Ha un
    solo parametro libero invece di un bosco intero, quindi non puo'
    sovradattare: se anche questa miscela non migliora il mercato, il problema
    non e' la forma del modello.

    In scala logaritmica e non lineare perche' e' li' che vive il modello
    Poisson: mediare i logaritmi combina i moltiplicatori di forza, mediare i
    livelli combinerebbe i conteggi attesi, che non e' la stessa cosa quando
    poi si esponenzia.

    Il peso si stima sulla sola VALIDAZIONE. w=0 significa mercato puro, w=1
    GBM puro.
    """

    def __init__(self, weight: float, **gbm_params) -> None:
        self.weight = weight
        self.inner = PoissonGBM(use_market=False, **gbm_params)

    @property
    def name(self) -> str:
        return f"M6 miscela log w={self.weight:.2f}"

    def fit(self, train: pd.DataFrame) -> "LogBlend":
        self.inner.fit(train)
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        inner = self.inner.predict(test)
        out = self._empty(test)
        ok = (
            inner[["lambda_home", "lambda_away"]].notna().all(axis=1)
            & test[list(MKT_LAMBDA)].notna().all(axis=1)
        ).to_numpy()
        if not ok.any():
            return out

        lam = []
        for gbm_col, mkt_col in zip(("lambda_home", "lambda_away"), MKT_LAMBDA):
            g = np.log(inner.loc[ok, gbm_col].to_numpy(dtype=float))
            m = np.log(test.loc[ok, mkt_col].to_numpy(dtype=float))
            lam.append(np.exp(self.weight * g + (1 - self.weight) * m))

        filled = predictions_from_lambdas(lam[0], lam[1], index=test.index[ok], rho=self.inner.rho)
        out.loc[filled.index, filled.columns] = filled
        return out


# ---------------------------------------------------------------------------

def space_for(variant: str) -> dict[str, list]:
    return ANCHORED_SEARCH_SPACE if variant == "ancorato" else SEARCH_SPACE


def sample_configs(n: int, seed: int = 0, space: dict[str, list] | None = None) -> list[dict]:
    """Configurazioni distinte estratte a caso dallo spazio di ricerca."""
    space = space or SEARCH_SPACE
    keys = list(space)
    full = list(itertools.product(*(space[k] for k in keys)))
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(full), size=min(n, len(full)), replace=False)
    return [dict(zip(keys, full[i])) for i in idx]


def on_boundary(cfg: dict, space: dict[str, list] | None = None) -> list[str]:
    """
    Quali parametri della configurazione vincente stanno sul bordo dello
    spazio esplorato. Se la lista non e' vuota, il minimo non e' stato trovato:
    va esteso quel parametro, e solo quello.
    """
    space = space or SEARCH_SPACE
    return [
        f"{k}={v} ({'min' if v == min(space[k]) else 'max'})"
        for k, v in cfg.items()
        if v in (min(space[k]), max(space[k]))
    ]


# Le tre varianti che si tarano: senza mercato, con mercato fra le feature,
# ancorata al mercato via init_score.
VARIANTS = {
    "senza": lambda cfg: PoissonGBM(use_market=False, **cfg),
    "con": lambda cfg: PoissonGBM(use_market=True, **cfg),
    "ancorato": lambda cfg: MarketAnchoredGBM(**cfg),
}


def _score_config(df: pd.DataFrame, variant: str, cfg: dict,
                  validation_seasons: list[str], stride: int) -> dict:
    """Una configurazione, un walk-forward di validazione, un RPS."""
    import logging as _logging

    # Nei processi figli il log del walk-forward e' solo rumore interlacciato.
    _logging.getLogger("evaluate").setLevel(_logging.WARNING)
    from ..evaluate import PROB_COLS, outcome_index, rps, walk_forward

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
    configs = sample_configs(n_configs, seed=seed)
    log.info("%d configurazioni x %d varianti = %d run, stride %d, %d processi",
             len(configs), len(variants), len(jobs), stride, n_workers)

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
    from ..evaluate import PROB_COLS, outcome_index, rps, walk_forward
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
    keys = ["league", "season", "home_team", "away_team"]
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


def feature_importance(
    df: pd.DataFrame,
    gbm_params: dict | None = None,
    seasons: list[str] | None = None,
    anchored: bool = False,
) -> pd.DataFrame:
    """
    Importanza per guadagno delle feature di M4 senza mercato.

    `anchored=True` usa invece M5, il GBM ancorato al mercato. E' la vista
    giusta per misurare un blocco nuovo: in M4 le feature competono per
    spiegare il LIVELLO dei gol, e vincono sempre quelle di forza; in M5 il
    livello lo mette gia' il mercato, quindi l'importanza si legge sul
    residuo — cioe' sull'unica cosa che un blocco nuovo puo' spiegare.

    Per guadagno e non per numero di split: contare gli split premia le
    variabili continue con molti valori distinti, che vengono usate spesso per
    tagli marginali. Il guadagno misura quanta devianza ciascuna variabile ha
    tolto davvero.

    Si media su un fit per stagione di test, presi al primo taglio di ciascuna:
    una sola istantanea sarebbe la fotografia di un addestramento particolare.
    """
    gbm_params = gbm_params or (config.GBM_PARAMS_ANCHORED if anchored
                                else config.GBM_PARAMS_NO_MARKET)
    seasons = seasons or config.TEST_SEASONS
    played = df[df["FTR"].notna()]

    frames = []
    for season in seasons:
        cutoff = df.loc[df["season"] == season, "date"].min()
        train = played[(played["date"] < cutoff) & (~played["season"].isin(config.BURN_IN_SEASONS))]
        model = (MarketAnchoredGBM(**gbm_params) if anchored
                 else PoissonGBM(use_market=False, **gbm_params)).fit(train)
        if model.model_home_ is None:
            log.warning("stagione %s: modello non addestrato, saltata", season)
            continue
        for lato, m in (("casa", model.model_home_), ("fuori", model.model_away_)):
            gain = m.booster_.feature_importance(importance_type="gain")
            frames.append(pd.Series(gain / gain.sum(), index=model.features_,
                                    name=f"{season}_{lato}"))

    tab = pd.concat(frames, axis=1)
    out = pd.DataFrame({
        "quota_media": tab.mean(axis=1),
        "min": tab.min(axis=1),
        "max": tab.max(axis=1),
    }).sort_values("quota_media", ascending=False)
    return out


def main() -> None:
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

    from ..evaluate import load_dataset
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


if __name__ == "__main__":
    main()
