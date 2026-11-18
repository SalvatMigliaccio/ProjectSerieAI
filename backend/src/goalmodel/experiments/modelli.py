"""
Modelli sperimentali: M5 che dichiara i propri set, e la media su piu' semi.

NESSUNA MODIFICA A `models/gbm.py`. `report.py` — produzione — costruisce M4
da quel modulo, e ogni ritocco alle sue classi o costanti cambierebbe il
report senza che nessuno lo decida. Qui si SOTTOCLASSA: la logica di
addestramento e previsione resta quella di `MarketAnchoredGBM`, verificata e
misurata, e cambia solo come si sceglie l'elenco di colonne.

M5Set
    M5 ancorato al mercato che dichiara i set: `M5Set(sets=["BASE"])`. Traduce
    i set nell'argomento `escludi` che la classe madre gia' capisce, al
    momento del fit — cosi' una colonna non registrata resta fuori per
    default invece di entrare di straforo.

M5MediaSemi
    Cinque M5Set identici tranne il seme, con la previsione MEDIATA in scala
    logaritmica. Non aggiunge informazione e quindi NON e' un test di
    ipotesi: riduce la varianza dovuta al campionamento di righe e colonne
    (`subsample` e `colsample_bytree` a 0.6), che su un effetto di 0.0003 RPS
    puo' decidere da che parte dello zero cade l'intervallo.

PERCHE' LA MEDIA E' SUI LOG-LAMBDA E NON SULLE PROBABILITA'
Ogni M5 produce `lambda = exp(log(lambda_mercato) + somma_alberi)`. Mediare
gli esponenti — cioe' la media geometrica dei lambda — equivale a mediare gli
alberi, e mantiene il modello dentro la famiglia Poisson a due gol: la
matrice dei risultati si ricostruisce dai lambda mediati, e 1X2, over/under e
punteggi esatti restano coerenti fra loro per costruzione. Mediare le
probabilita' 1X2 darebbe un'altra cosa: un miscuglio di cinque matrici che
non corrisponde a nessuna coppia di lambda.

Proprieta' usata dall'analisi: la media dei semi si puo' ricostruire ESATTA a
posteriori dalle previsioni dei singoli semi. `media_log_lambda` e' la stessa
funzione in entrambi i casi, e `tests/test_experiments_modelli.py` verifica
che le due strade diano gli stessi numeri.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from ..features import sets as sets_mod
from ..models.baseline import PRED_COLS, Model, predictions_from_lambdas
from ..models.gbm import MarketAnchoredGBM, form_features


class M5Set(MarketAnchoredGBM):
    """M5 ancorato al mercato, con le colonne scelte per set dichiarati."""

    def __init__(self, sets, seed: int = 0, togli: tuple[str, ...] = (),
                 etichetta: str | None = None, **params) -> None:
        parametri = {**config.GBM_PARAMS_ANCHORED, **params}
        super().__init__(escludi=(), seed=seed, **parametri)
        self.sets = tuple(sets)
        # Colonne da togliere DENTRO i set dichiarati: serve alle verifiche di
        # robustezza, dove si vuole un set meno qualcosa, non un set nuovo.
        self.togli = tuple(togli)
        nome = etichetta or "+".join(self.sets)
        self.suffisso = f" [{nome}] s{seed}"

    def fit(self, train: pd.DataFrame) -> M5Set:
        dichiarate = set(sets_mod.colonne(self.sets))
        estranee = [c for c in self.togli if c not in dichiarate]
        if estranee:
            raise ValueError(f"togli contiene colonne fuori dai set {self.sets}: {estranee}")

        candidate = form_features(train, escludi=())
        self.escludi = tuple(sorted(
            set(sets_mod.escludi_per(candidate, self.sets)) | set(self.togli)
        ))
        return super().fit(train)


class M5Colonne(MarketAnchoredGBM):
    """
    M5 con un elenco ESPLICITO di colonne, per le rappresentazioni derivate.

    Serve quando le colonne non esistono nel dataset ma vengono costruite dentro
    l'esperimento — le componenti principali di un blocco di BASE, per esempio.
    Il registro dei set non le conosce, e `escludi_per` le escluderebbe tutte:
    e' il comportamento giusto per il codice normale, sbagliato qui. Se una
    rappresentazione si dimostra utile, diventa un set registrato; finche' e'
    un'esplorazione, l'elenco esplicito sta nel codice dell'esperimento, dove
    si legge.
    """

    def __init__(self, colonne, seed: int = 0, etichetta: str = "colonne",
                 **params) -> None:
        parametri = {**config.GBM_PARAMS_ANCHORED, **params}
        super().__init__(escludi=(), seed=seed, **parametri)
        self.colonne = tuple(colonne)
        self.suffisso = f" [{etichetta}] s{seed}"

    def fit(self, train: pd.DataFrame) -> M5Colonne:
        assenti = [c for c in self.colonne if c not in train.columns]
        if assenti:
            raise KeyError(f"colonne dichiarate assenti dal training: {assenti[:5]}")
        tenute = set(self.colonne)
        candidate = form_features(train, escludi=())
        self.escludi = tuple(c for c in candidate if c not in tenute)
        return super().fit(train)


def media_log_lambda(previsioni: list[pd.DataFrame], index: pd.Index,
                     rho: float = config.DC_RHO) -> pd.DataFrame:
    """
    Media geometrica dei lambda di piu' modelli, poi la matrice dei risultati.

    Una riga resta NaN se almeno un modello non l'ha prevista: mediare su un
    numero di semi diverso da riga a riga produrrebbe un modello diverso per
    ogni partita.
    """
    lh = np.vstack([np.log(p["lambda_home"].to_numpy(float)) for p in previsioni])
    la = np.vstack([np.log(p["lambda_away"].to_numpy(float)) for p in previsioni])
    mh, ma = np.exp(lh.mean(axis=0)), np.exp(la.mean(axis=0))

    out = pd.DataFrame(np.nan, index=index, columns=PRED_COLS)
    ok = np.isfinite(mh) & np.isfinite(ma)
    if ok.any():
        pieni = predictions_from_lambdas(mh[ok], ma[ok], index=index[ok], rho=rho)
        out.loc[pieni.index, PRED_COLS] = pieni
    return out


class M5MediaSemi(Model):
    """La media di piu' M5Set che differiscono solo per il seme."""

    def __init__(self, sets, semi: tuple[int, ...] = (0, 1, 2, 3, 4),
                 togli: tuple[str, ...] = (), etichetta: str | None = None) -> None:
        self.sets = tuple(sets)
        self.semi = tuple(semi)
        self.modelli = [M5Set(sets, seed=s, togli=togli, etichetta=etichetta)
                        for s in self.semi]
        nome = etichetta or "+".join(self.sets)
        self._name = f"M5 media {len(self.semi)} semi [{nome}]"

    @property
    def name(self) -> str:
        return self._name

    def fit(self, train: pd.DataFrame) -> M5MediaSemi:
        for m in self.modelli:
            m.fit(train)
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        return media_log_lambda([m.predict(test) for m in self.modelli], test.index)
