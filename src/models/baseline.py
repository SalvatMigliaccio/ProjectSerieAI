"""
Modelli di riferimento. Servono a dare un pavimento e un soffitto: finche' un
modello nuovo non batte questi, non ha diritto di esistere.

M0  vince sempre la casa. Il pavimento banale.
M0b frequenze di base stimate sul training. Il vero pavimento probabilistico:
    M0 e' indifendibile sulla log loss (dà probabilita' zero a esiti che
    accadono), quindi da solo non basta a dire se un modello e' informativo.
M1  market-only: le quote de-viggate, riportate sulla scala dei gol da
    features/market.py. E' il soffitto realistico, non un pavimento.
M2  GLM Poisson con forza d'attacco e di difesa per squadra piu' vantaggio
    casa, stimato su finestra mobile. E' il primo modello vero.

L'INTERFACCIA
Ogni modello espone `fit(train)` e `predict(test)`. `predict` restituisce
sempre le stesse colonne, cosi' l'harness di valutazione non deve sapere
niente di chi sta valutando:

    p_home, p_draw, p_away, p_over25, p_under25, lambda_home, lambda_away

Chi non ragiona a gol (M0, M0b) lascia i lambda a NaN.

PERCHE' TUTTO PASSA DALLA MATRICE DEI RISULTATI ESATTI
Il target sono i gol, non l'1X2 (decisione bloccata). Un modello produce due
lambda; l'1X2 e l'over/under sono letture diverse della stessa matrice
lambda -> P(i gol in casa, j gol fuori). Costruire l'1X2 in un altro modo
significherebbe avere due modelli incoerenti fra loro.

Uso:
    python -m src.models.baseline --demo
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor

from .. import config

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("baseline")

PRED_COLS = [
    "p_home", "p_draw", "p_away",
    "p_over25", "p_under25",
    "lambda_home", "lambda_away",
]


# ---------------------------------------------------------------------------
# Da lambda alla matrice dei risultati, e dalla matrice a tutto il resto
# ---------------------------------------------------------------------------

def valid_rho_floor(lam_home: np.ndarray, lam_away: np.ndarray, margine: float = 0.95) -> np.ndarray:
    """
    Il rho piu' negativo ammissibile, riga per riga.

    La correzione di Dixon-Coles non e' valida per ogni rho: e' una
    moltiplicazione, e se tau diventa negativo esce una probabilita' negativa.
    Con rho < 0 i vincoli che mordono sono tau(0,1) = 1 + lam*rho > 0 e
    tau(1,0) = 1 + mu*rho > 0, cioe' rho > -1 / max(lam, mu). Con lambda 3.5
    significa rho > -0.29: un limite che si tocca davvero, non teorico.
    """
    return -margine / np.maximum(np.maximum(lam_home, lam_away), 1e-9)


def score_matrix(
    lam_home: np.ndarray,
    lam_away: np.ndarray,
    max_goals: int = config.MAX_GOALS,
    rho: float | np.ndarray = 0.0,
) -> np.ndarray:
    """
    Matrice dei risultati esatti, una per riga: shape (n, K+1, K+1).

    Sotto Poisson indipendenti P(i, j) = P(i | lam_casa) * P(j | lam_fuori).
    La correzione di Dixon-Coles interviene sui quattro punteggi bassi, dove
    l'indipendenza sbaglia di piu': nel calcio 0-0 e 1-1 sono piu' frequenti
    di quanto due Poisson indipendenti prevedano, perche' il punteggio stesso
    cambia il modo di giocare. Con rho=0 la correzione e' l'identita' e si
    ricade sull'indipendenza: e' il default, perche' rho va stimato sui dati e
    quello e' il mestiere di dixon_coles.py, non di un baseline.

    ATTENZIONE AL SEGNO DI RHO. Con la parametrizzazione classica
        tau(0,0) = 1 - lam_casa*lam_fuori*rho
        tau(0,1) = 1 + lam_casa*rho
        tau(1,0) = 1 + lam_fuori*rho
        tau(1,1) = 1 - rho
    e' rho NEGATIVO ad alzare 0-0 e 1-1 e ad abbassare 1-0 e 0-1, cioe' a
    produrre l'effetto che si osserva davvero nel calcio. Dixon e Coles nel
    1997 stimarono rho = -0.13. Un rho positivo fa l'esatto contrario: e' un
    errore silenzioso, perche' la matrice resta valida e somma a 1.

    La correzione conserva la massa totale per costruzione (i quattro termini
    si cancellano), ma la si rinormalizza comunque: la matrice e' troncata a
    max_goals e un po' di massa nella coda si perde davvero.
    """
    lam_home = np.asarray(lam_home, dtype=float)
    lam_away = np.asarray(lam_away, dtype=float)
    goals = np.arange(max_goals + 1)

    ph = poisson.pmf(goals[None, :], lam_home[:, None])
    pa = poisson.pmf(goals[None, :], lam_away[:, None])
    mat = ph[:, :, None] * pa[:, None, :]

    rho_arr = np.broadcast_to(np.asarray(rho, dtype=float), lam_home.shape)
    if np.any(rho_arr != 0.0):
        tau = np.ones_like(mat)
        tau[:, 0, 0] = 1.0 - lam_home * lam_away * rho_arr
        tau[:, 0, 1] = 1.0 + lam_home * rho_arr
        tau[:, 1, 0] = 1.0 + lam_away * rho_arr
        tau[:, 1, 1] = 1.0 - rho_arr
        mat = mat * tau
        # Fuori dal dominio valido tau diventa negativo e con lui la
        # probabilita'. Si solleva invece di tagliare in silenzio: chi chiama
        # deve sapere che il suo rho non e' ammissibile per quei lambda, e
        # `valid_rho_floor` gli dice qual e' il limite.
        if (mat < 0).any():
            peggiore = float(rho_arr.ravel()[np.argmin(mat.min(axis=(1, 2)))])
            raise ValueError(
                f"rho={peggiore:.4f} produce probabilita' negative: fuori dal dominio "
                f"valido. Il minimo ammissibile per questi lambda e' "
                f"{valid_rho_floor(lam_home, lam_away).min():.4f}"
            )

    total = mat.sum(axis=(1, 2), keepdims=True)
    return mat / total


def outcomes_from_matrix(mat: np.ndarray, line: float = 2.5) -> pd.DataFrame:
    """1X2 e over/under letti dalla stessa matrice: sono viste, non modelli."""
    k = mat.shape[1]
    idx = np.arange(k)
    home_win = idx[:, None] > idx[None, :]
    draw = idx[:, None] == idx[None, :]
    away_win = idx[:, None] < idx[None, :]
    over = (idx[:, None] + idx[None, :]) > line

    return pd.DataFrame({
        "p_home": (mat * home_win).sum(axis=(1, 2)),
        "p_draw": (mat * draw).sum(axis=(1, 2)),
        "p_away": (mat * away_win).sum(axis=(1, 2)),
        "p_over25": (mat * over).sum(axis=(1, 2)),
        "p_under25": (mat * ~over).sum(axis=(1, 2)),
    })


def predictions_from_lambdas(
    lam_home: np.ndarray,
    lam_away: np.ndarray,
    index: pd.Index,
    rho: float = config.DC_RHO,
) -> pd.DataFrame:
    """Scorciatoia: dai due lambda a tutte le colonne di PRED_COLS."""
    mat = score_matrix(lam_home, lam_away, rho=rho)
    out = outcomes_from_matrix(mat)
    out["lambda_home"] = lam_home
    out["lambda_away"] = lam_away
    out.index = index
    return out[PRED_COLS]


# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------

class Model:
    """Interfaccia comune. `fit` puo' non fare niente, `predict` mai."""

    name = "modello"

    def fit(self, train: pd.DataFrame) -> "Model":
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def _empty(self, test: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(np.nan, index=test.index, columns=PRED_COLS)


class AlwaysHome(Model):
    """
    M0: vince sempre la casa, con certezza.

    Non e' un modello, e' un promemoria. Ha un'accuratezza rispettabile perche'
    il fattore campo esiste, e una log loss infinita perche' assegna
    probabilita' zero a due esiti su tre che accadono nel 58% dei casi. E' la
    dimostrazione, in una riga di tabella, del perche' l'accuratezza non
    seleziona modelli (regola 3).
    """

    name = "M0 sempre casa"

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(test)
        out["p_home"], out["p_draw"], out["p_away"] = 1.0, 0.0, 0.0
        return out


class BaseRate(Model):
    """
    M0b: le frequenze di 1, X, 2 osservate nel training, uguali per tutti.

    E' il vero pavimento probabilistico: qualsiasi modello che non lo batte
    non ha estratto alcuna informazione dalla partita specifica. L'over/under
    segue la stessa logica, con la frequenza di over osservata.
    """

    name = "M0b frequenze di base"

    def __init__(self) -> None:
        self.rates = np.array([np.nan, np.nan, np.nan])
        self.p_over = np.nan

    def fit(self, train: pd.DataFrame) -> "BaseRate":
        ftr = train["FTR"].dropna()
        self.rates = np.array([(ftr == c).mean() for c in ("H", "D", "A")])
        tot = (train["FTHG"] + train["FTAG"]).dropna()
        self.p_over = float((tot > 2.5).mean())
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(test)
        out["p_home"], out["p_draw"], out["p_away"] = self.rates
        out["p_over25"] = self.p_over
        out["p_under25"] = 1.0 - self.p_over
        return out


class MarketOnly(Model):
    """
    M1: solo il mercato. Non si addestra: legge i lambda impliciti nelle quote
    di apertura, gia' calcolati da features/market.py, e li fa passare per la
    stessa matrice dei risultati usata da tutti gli altri.

    Passare dai lambda invece che usare direttamente le probabilita' de-viggate
    costa qualcosa: il giro d'andata e ritorno attraverso due Poisson
    indipendenti non e' esatto. E' voluto, perche' cosi' M1 e' confrontabile
    con i modelli a gol sulla stessa base. La versione diretta e' MarketDirect,
    e la differenza fra le due misura proprio quel costo.
    """

    name = "M1 market-only (lambda)"

    def __init__(self, rho: float = config.DC_RHO) -> None:
        self.rho = rho

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        lam_h = test["mkt_lambda_home"].to_numpy(dtype=float)
        lam_a = test["mkt_lambda_away"].to_numpy(dtype=float)
        ok = np.isfinite(lam_h) & np.isfinite(lam_a)
        out = self._empty(test)
        if ok.any():
            filled = predictions_from_lambdas(
                lam_h[ok], lam_a[ok], index=test.index[ok], rho=self.rho
            )
            out.loc[filled.index, PRED_COLS] = filled
        return out


class MarketDirect(Model):
    """
    M1b: le probabilita' de-viggate cosi' come sono, senza passare dai gol.
    E' il benchmark market-only nella sua forma piu' pura, e il riferimento
    contro cui misurare tutto il resto.
    """

    name = "M1b market-only (diretto)"

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        out = self._empty(test)
        for dst, src in (
            ("p_home", "mkt_p_home"), ("p_draw", "mkt_p_draw"), ("p_away", "mkt_p_away"),
            ("p_over25", "mkt_p_over25"), ("p_under25", "mkt_p_under25"),
            ("lambda_home", "mkt_lambda_home"), ("lambda_away", "mkt_lambda_away"),
        ):
            out[dst] = test[src].to_numpy(dtype=float)
        return out


class PoissonGLM(Model):
    """
    M2: log(gol attesi) = intercetta + attacco[chi segna] + difesa[chi subisce]
    + vantaggio casa.

    FORMATO LUNGO
    Ogni partita diventa due osservazioni, una per lato. E' l'unico modo di
    stimare un attacco e una difesa per squadra con un solo GLM invece di due.

    PERCHE' UNA RIDGE MINIMA E NON LA MASSIMA VEROSIMIGLIANZA PURA
    Intercetta e dummy di attacco sono collineari per costruzione: la somma
    delle dummy di attacco vale 1 su ogni riga. Senza vincoli il problema e'
    indeterminato lungo una direzione. La soluzione classica e' imporre somma
    zero sui coefficienti; una ridge infinitesima fa la stessa cosa (sceglie
    la soluzione di norma minima) senza codice in piu' e senza cambiare le
    previsioni, che dipendono solo dalle differenze fra coefficienti.

    FINESTRA MOBILE
    Si stima sulle ultime `window` partite di lega, non su tutto lo storico:
    una squadra del 2016 non dice niente sulla stessa squadra del 2024. E' il
    sostituto povero del decadimento temporale di Dixon-Coles.

    SQUADRE MAI VISTE
    Una neopromossa senza partite nella finestra riceve riga tutta zeri, cioe'
    attacco e difesa medi di lega. E' una scelta prudente e sbagliata in modo
    noto (le neopromosse sono sotto la media): la correggera' il modulo dei
    priori, non questo.
    """

    name = "M2 GLM Poisson"

    def __init__(
        self,
        window: int = config.GLM_WINDOW_MATCHES,
        ridge: float = config.GLM_RIDGE,
        rho: float = config.DC_RHO,
    ) -> None:
        self.window = window
        self.ridge = ridge
        self.rho = rho
        self.teams_: list[str] = []
        self.model_: PoissonRegressor | None = None

    def _design(self, attack: pd.Series, defence: pd.Series, is_home: np.ndarray) -> np.ndarray:
        n_t = len(self.teams_)
        pos = {t: i for i, t in enumerate(self.teams_)}
        x = np.zeros((len(attack), 2 * n_t + 1))
        for r, (a, d) in enumerate(zip(attack, defence)):
            if a in pos:
                x[r, pos[a]] = 1.0
            if d in pos:
                x[r, n_t + pos[d]] = 1.0
        x[:, -1] = is_home
        return x

    def fit(self, train: pd.DataFrame) -> "PoissonGLM":
        df = train.dropna(subset=["FTHG", "FTAG"]).sort_values("date")
        if self.window:
            df = df.tail(self.window)
        if df.empty:
            self.model_ = None
            return self

        # Formato lungo: due righe per partita.
        attack = pd.concat([df["home_team"], df["away_team"]], ignore_index=True)
        defence = pd.concat([df["away_team"], df["home_team"]], ignore_index=True)
        y = pd.concat([df["FTHG"], df["FTAG"]], ignore_index=True).to_numpy(dtype=float)
        is_home = np.r_[np.ones(len(df)), np.zeros(len(df))]

        self.teams_ = sorted(set(attack) | set(defence))
        x = self._design(attack, defence, is_home)

        self.model_ = PoissonRegressor(alpha=self.ridge, fit_intercept=True, max_iter=500)
        self.model_.fit(x, y)
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        if self.model_ is None:
            return self._empty(test)
        n = len(test)
        x_home = self._design(test["home_team"], test["away_team"], np.ones(n))
        x_away = self._design(test["away_team"], test["home_team"], np.zeros(n))
        lam_h = self.model_.predict(x_home)
        lam_a = self.model_.predict(x_away)
        return predictions_from_lambdas(lam_h, lam_a, index=test.index, rho=self.rho)


def default_models() -> list[Model]:
    """Il set di riferimento, nell'ordine in cui va letta la tabella."""
    return [AlwaysHome(), BaseRate(), MarketDirect(), MarketOnly(), PoissonGLM()]


# ---------------------------------------------------------------------------

def _demo() -> None:
    """Controlli di coerenza sulla matrice, senza toccare i dati veri."""
    lam_h = np.array([1.5, 2.0, 0.8])
    lam_a = np.array([1.2, 0.9, 1.6])

    mat = score_matrix(lam_h, lam_a)
    assert np.allclose(mat.sum(axis=(1, 2)), 1.0), "la matrice non somma a 1"

    out = outcomes_from_matrix(mat)
    assert np.allclose(out[["p_home", "p_draw", "p_away"]].sum(axis=1), 1.0), "1X2 non somma a 1"
    assert np.allclose(out[["p_over25", "p_under25"]].sum(axis=1), 1.0), "over/under non somma a 1"

    # I gol attesi dalla matrice devono ridare i lambda, ma solo a meno del
    # troncamento: oltre MAX_GOALS la coda viene tagliata e la rinormalizzazione
    # non la restituisce. A MAX_GOALS=10 il difetto vale 8e-5 gol per lambda=2
    # e 2e-2 per lambda=4, cioe' mezzo punto percentuale nel caso peggiore che
    # la Serie A produca. Irrilevante per 1X2 e over/under, che e' cio' che si
    # legge dalla matrice; da rivedere se un giorno servissero le code.
    goals = np.arange(mat.shape[1])
    exp_h = (mat.sum(axis=2) * goals).sum(axis=1)
    exp_a = (mat.sum(axis=1) * goals).sum(axis=1)
    assert np.allclose(exp_h, lam_h, rtol=1e-3), "gol attesi in casa non tornano"
    assert np.allclose(exp_a, lam_a, rtol=1e-3), "gol attesi fuori non tornano"
    assert (exp_h <= lam_h + 1e-12).all(), "il troncamento puo' solo togliere gol, non aggiungerne"

    # Dixon-Coles: e' rho NEGATIVO ad alzare 0-0 e 1-1, ed e' il segno che si
    # stima sui dati veri. Il controllo verifica proprio il segno, perche' e'
    # l'errore piu' facile da fare e il piu' difficile da vedere a valle.
    dc = score_matrix(lam_h, lam_a, rho=-0.10)
    assert np.allclose(dc.sum(axis=(1, 2)), 1.0), "la matrice DC non somma a 1"
    assert (dc[:, 0, 0] > mat[:, 0, 0]).all(), "con rho<0 lo 0-0 deve salire"
    assert (dc[:, 1, 1] > mat[:, 1, 1]).all(), "con rho<0 l'1-1 deve salire"
    assert (dc[:, 1, 0] < mat[:, 1, 0]).all(), "con rho<0 l'1-0 deve scendere"
    assert (dc[:, 0, 1] < mat[:, 0, 1]).all(), "con rho<0 lo 0-1 deve scendere"

    # E il pareggio nel complesso deve guadagnare: e' tutto il punto di DC.
    assert (outcomes_from_matrix(dc)["p_draw"] > out["p_draw"]).all(), \
        "DC con rho<0 deve alzare la probabilita' di pareggio"

    # rho per riga: serve a Dixon-Coles, che taglia rho al dominio valido di
    # ogni singola partita. Un rho per riga costante deve dare lo stesso
    # risultato dello scalare.
    per_riga = score_matrix(lam_h, lam_a, rho=np.full(3, -0.10))
    assert np.allclose(per_riga, dc), "rho per riga non coincide con rho scalare"
    misto = score_matrix(lam_h, lam_a, rho=np.array([-0.10, 0.0, -0.05]))
    assert np.allclose(misto[1], mat[1]), "rho=0 su una riga deve lasciarla intatta"

    # Il dominio: sotto il pavimento la correzione non e' piu' una probabilita'
    # e la funzione deve rifiutarsi, non produrre numeri negativi in silenzio.
    floor = valid_rho_floor(lam_h, lam_a)
    assert (floor < 0).all()
    try:
        score_matrix(lam_h, lam_a, rho=float(floor.min() * 2))
    except ValueError:
        pass
    else:
        raise AssertionError("un rho fuori dominio doveva sollevare ValueError")

    log.info("controlli sulla matrice dei risultati superati")
    log.info("esempio lambda (%.2f, %.2f):\n%s", lam_h[0], lam_a[0], out.iloc[0].round(4).to_string())


def main() -> None:
    ap = argparse.ArgumentParser(description="Modelli di riferimento")
    ap.add_argument("--demo", action="store_true", help="controlli di coerenza sulla matrice")
    args = ap.parse_args()
    if args.demo:
        _demo()
    else:
        log.info("modelli disponibili: %s", [m.name for m in default_models()])
        log.info("la valutazione si lancia con: python -m src.evaluate")


if __name__ == "__main__":
    main()
