"""
M3: Dixon-Coles con decadimento temporale.

COS'E' IN PIU' RISPETTO AL GLM POISSON DI M2
Due cose, ed e' utile tenerle distinte perche' contribuiscono in modo diverso.

1. Il decadimento temporale. M2 usa una finestra mobile netta: le ultime 760
   partite contano uguale, la 761esima non conta niente. Qui ogni partita pesa
   0.5 ** (giorni_fa / half-life): il passato sfuma invece di cadere da un
   dirupo. La half-life si tara, e si tara sulla VALIDAZIONE.

2. Il parametro rho. Due Poisson indipendenti sbagliano sistematicamente sui
   punteggi bassi: 0-0 e 1-1 accadono piu' spesso di quanto prevedano, perche'
   il punteggio corrente cambia il modo di giocare. Rho corregge quelle quattro
   celle ed e' stimato INSIEME ad attacchi e difese, non fissato a priori:
   massimizzare la verosimiglianza in due tempi darebbe un rho tarato su
   parametri sbagliati.

LA VEROSIMIGLIANZA
    log L = somma_i  w_i * [ log tau(x_i, y_i) + x_i log lam_i - lam_i
                                              + y_i log mu_i  - mu_i ]
con
    log lam = mu0 + attacco[casa] + difesa[fuori] + vantaggio_casa
    log mu  = mu0 + attacco[fuori] + difesa[casa]

IDENTIFICABILITA'
Attacchi, difese e intercetta sono collineari: si puo' aggiungere una costante
a tutti gli attacchi e toglierla dall'intercetta senza cambiare nulla. Si
ancora con una penalita' sulla somma dei coefficienti, che equivale al vincolo
classico di somma zero ma non richiede riparametrizzare.

IL GRADIENTE E' ANALITICO
Con 42 parametri, una differenza finita costa 43 valutazioni per iterazione.
Il gradiente analitico ne costa una. Su ~500 riaddestramenti fra taratura e
test la differenza e' fra minuti e ore.

Uso:
    python -m src.models.dixon_coles --tune     # tara la half-life sulla validazione
    python -m src.models.dixon_coles --check    # verifica il gradiente
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .. import config
from .baseline import Model, predictions_from_lambdas, valid_rho_floor

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("dixon_coles")

# Sotto questa soglia tau viene tagliato. Serve solo a impedire il logaritmo di
# un numero negativo durante l'ottimizzazione: la regione e' comunque pessima
# in verosimiglianza e l'ottimizzatore ne esce da solo.
TAU_FLOOR = 1e-8


def _tau_and_grad(
    x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Correzione di Dixon-Coles e le sue derivate rispetto a log lam, log mu, rho.

    Le derivate sono rispetto ai LOGARITMI di lam e mu perche' e' cosi' che
    entrano nel modello lineare: risparmia una moltiplicazione per lam a valle.
    """
    tau = np.ones_like(lam)
    d_loglam = np.zeros_like(lam)
    d_logmu = np.zeros_like(lam)
    d_rho = np.zeros_like(lam)

    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)

    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    tau[m01] = 1.0 + lam[m01] * rho
    tau[m10] = 1.0 + mu[m10] * rho
    tau[m11] = 1.0 - rho
    tau = np.maximum(tau, TAU_FLOOR)

    # d tau / d log lam = lam * d tau / d lam
    d_loglam[m00] = -lam[m00] * mu[m00] * rho
    d_loglam[m01] = lam[m01] * rho
    d_logmu[m00] = -lam[m00] * mu[m00] * rho
    d_logmu[m10] = mu[m10] * rho

    d_rho[m00] = -lam[m00] * mu[m00]
    d_rho[m01] = lam[m01]
    d_rho[m10] = mu[m10]
    d_rho[m11] = -1.0

    return tau, d_loglam / tau, d_logmu / tau, d_rho / tau


def _nll(
    params: np.ndarray,
    idx_h: np.ndarray,
    idx_a: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    n_teams: int,
    ridge: float,
) -> tuple[float, np.ndarray]:
    """Log-verosimiglianza negativa pesata, con gradiente analitico."""
    mu0 = params[0]
    atk = params[1:1 + n_teams]
    dfn = params[1 + n_teams:1 + 2 * n_teams]
    gamma = params[-2]
    rho = params[-1]

    log_lam = mu0 + atk[idx_h] + dfn[idx_a] + gamma
    log_mu = mu0 + atk[idx_a] + dfn[idx_h]
    lam = np.exp(log_lam)
    mu = np.exp(log_mu)

    tau, dt_loglam, dt_logmu, dt_rho = _tau_and_grad(x, y, lam, mu, rho)

    ll = w * (np.log(tau) + x * log_lam - lam + y * log_mu - mu)
    penalty = ridge * (atk.sum() ** 2 + dfn.sum() ** 2)

    # d ll / d log lam per riga, e idem per log mu.
    g_loglam = w * (dt_loglam + x - lam)
    g_logmu = w * (dt_logmu + y - mu)

    grad = np.zeros_like(params)
    grad[0] = g_loglam.sum() + g_logmu.sum()
    # L'attacco della squadra di casa entra in lam, quello della trasferta in mu.
    grad[1:1 + n_teams] = (
        np.bincount(idx_h, weights=g_loglam, minlength=n_teams)
        + np.bincount(idx_a, weights=g_logmu, minlength=n_teams)
    )
    grad[1 + n_teams:1 + 2 * n_teams] = (
        np.bincount(idx_a, weights=g_loglam, minlength=n_teams)
        + np.bincount(idx_h, weights=g_logmu, minlength=n_teams)
    )
    grad[-2] = g_loglam.sum()
    grad[-1] = (w * dt_rho).sum()

    grad[1:1 + n_teams] -= 2 * ridge * atk.sum()
    grad[1 + n_teams:1 + 2 * n_teams] -= 2 * ridge * dfn.sum()

    return -(ll.sum() - penalty), -grad


class DixonColes(Model):
    """
    M3. `halflife_days` e' l'unico iperparametro, e va tarato sulla validazione.

    NEUTRALITA' SULLE SQUADRE MAI VISTE
    Una neopromossa senza partite nella finestra pesata riceve attacco e difesa
    nulli, cioe' i valori medi di lega. Stessa scelta prudente e stesso errore
    noto di M2: le neopromosse sono sotto la media. Lo correggera' il modulo
    dei priori.
    """

    def __init__(
        self,
        halflife_days: float = 90.0,
        ridge: float = 1.0,
        max_history_days: float = 1460.0,
    ) -> None:
        self.halflife_days = halflife_days
        self.ridge = ridge
        # Oltre quattro anni il peso e' sotto lo 0.1% anche con half-life 180:
        # tenere quelle righe costa tempo e non cambia la stima.
        self.max_history_days = max_history_days
        self.teams_: list[str] = []
        self.params_: np.ndarray | None = None
        self.rho_ = np.nan
        self.gamma_ = np.nan

    @property
    def name(self) -> str:
        return f"M3 Dixon-Coles hl={self.halflife_days:g}g"

    def fit(self, train: pd.DataFrame) -> "DixonColes":
        df = train.dropna(subset=["FTHG", "FTAG"])
        if df.empty:
            self.params_ = None
            return self

        cutoff = df["date"].max()
        age = (cutoff - df["date"]).dt.total_seconds().to_numpy() / 86400.0
        keep = age <= self.max_history_days
        df, age = df[keep], age[keep]

        # I pesi sono relativi all'ultima partita del training. Il punto di
        # riferimento e' indifferente: spostarlo moltiplica tutti i pesi per la
        # stessa costante, e una verosimiglianza pesata riscalata ha lo stesso
        # massimo. Conta solo il rapporto fra i pesi, cioe' la half-life.
        w = 0.5 ** (age / self.halflife_days)

        self.teams_ = sorted(set(df["home_team"]) | set(df["away_team"]))
        pos = {t: i for i, t in enumerate(self.teams_)}
        idx_h = df["home_team"].map(pos).to_numpy()
        idx_a = df["away_team"].map(pos).to_numpy()
        x = df["FTHG"].to_numpy(dtype=float)
        y = df["FTAG"].to_numpy(dtype=float)

        n_t = len(self.teams_)
        p0 = np.zeros(2 * n_t + 3)
        p0[0] = np.log(max((x.mean() + y.mean()) / 2.0, 0.1))
        p0[-2] = 0.25   # vantaggio casa, ~1.3 volte i gol
        p0[-1] = -0.05  # rho, del segno che si osserva nel calcio

        # Il limite inferiore su rho non e' arbitrario: e' il dominio in cui la
        # correzione resta una probabilita'. Serve rho > -1/max(lam, mu), e con
        # lambda fino a 4 gol questo da' -0.25. Con half-life molto corte il
        # campione efficace scende a poche decine di partite e la stima corre
        # verso il bordo: e' un sintomo di sovradattamento, non un valore da
        # prendere sul serio, e la taratura infatti scarta quelle half-life.
        bounds = [(None, None)] * (2 * n_t + 2) + [(-0.25, 0.1)]
        res = minimize(
            _nll, p0, jac=True, method="L-BFGS-B", bounds=bounds,
            args=(idx_h, idx_a, x, y, w, n_t, self.ridge),
            options={"maxiter": 500},
        )
        self.params_ = res.x
        self.gamma_ = float(res.x[-2])
        self.rho_ = float(res.x[-1])
        return self

    def lambdas(self, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        n_t = len(self.teams_)
        pos = {t: i for i, t in enumerate(self.teams_)}
        mu0 = self.params_[0]
        atk = self.params_[1:1 + n_t]
        dfn = self.params_[1 + n_t:1 + 2 * n_t]
        gamma = self.params_[-2]

        def coef(names: pd.Series, table: np.ndarray) -> np.ndarray:
            i = names.map(pos)
            out = np.where(i.isna(), 0.0, table[i.fillna(0).astype(int)])
            return out

        lam = np.exp(mu0 + coef(test["home_team"], atk) + coef(test["away_team"], dfn) + gamma)
        mu = np.exp(mu0 + coef(test["away_team"], atk) + coef(test["home_team"], dfn))
        return lam, mu

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        if self.params_ is None:
            return self._empty(test)
        lam, mu = self.lambdas(test)
        # Il rho stimato entra anche nella matrice: sarebbe incoerente stimarlo
        # e poi buttarlo via al momento di ricavare 1X2 e over/under.
        # Si taglia pero' al dominio valido per QUESTI lambda: una partita con
        # lambda alto ammette meno correzione di una da 1-0. Il taglio morde
        # solo quando la stima e' gia' andata a sbattere contro il bordo.
        floor = valid_rho_floor(lam, mu)
        rho = np.maximum(self.rho_, floor)
        n_clamped = int((rho > self.rho_ + 1e-12).sum())
        if n_clamped:
            log.debug("rho %.4f tagliato al dominio valido su %d righe", self.rho_, n_clamped)
        return predictions_from_lambdas(lam, mu, index=test.index, rho=rho)


# ---------------------------------------------------------------------------

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
    from ..evaluate import outcome_index, rps, walk_forward, PROB_COLS

    grid = grid or config.DC_HALFLIFE_GRID
    validation_seasons = validation_seasons or config.VALIDATION_SEASONS

    rows = []
    for hl in grid:
        preds = walk_forward(df, [DixonColes(halflife_days=hl)], test_seasons=validation_seasons)
        ok = preds.dropna(subset=PROB_COLS)
        score = rps(ok[PROB_COLS].to_numpy(dtype=float), outcome_index(ok["FTR"])).mean()
        rows.append({"half_life_giorni": hl, "RPS_validazione": float(score), "n": len(ok)})
        log.info("half-life %3g giorni -> RPS validazione %.6f", hl, score)

    tab = pd.DataFrame(rows).sort_values("RPS_validazione").reset_index(drop=True)
    log.info("scelta: half-life %g giorni", tab.loc[0, "half_life_giorni"])
    return tab


def _check_gradient() -> None:
    """Il gradiente analitico contro la differenza finita. Se sbaglia qui, il
    modello converge a qualcosa che non e' il massimo di verosimiglianza."""
    rng = np.random.default_rng(0)
    n_t, n = 8, 200
    idx_h = rng.integers(0, n_t, n)
    idx_a = (idx_h + 1 + rng.integers(0, n_t - 1, n)) % n_t
    x = rng.poisson(1.5, n).astype(float)
    y = rng.poisson(1.2, n).astype(float)
    w = 0.5 ** (rng.uniform(0, 3, n))
    p = rng.normal(0, 0.2, 2 * n_t + 3)
    p[0], p[-2], p[-1] = 0.2, 0.25, -0.07

    args = (idx_h, idx_a, x, y, w, n_t, 1.0)
    _, grad = _nll(p, *args)

    # Differenza CENTRATA, non in avanti. Con la differenza in avanti di
    # approx_fprime l'errore di troncamento e' O(h) e su questa funzione vale
    # ~1e-4: si finirebbe per inseguire un errore del metodo di verifica
    # credendolo un errore del gradiente. Centrata l'errore e' O(h^2).
    h = 1e-6
    num = np.empty_like(p)
    for i in range(len(p)):
        pp, pm = p.copy(), p.copy()
        pp[i] += h
        pm[i] -= h
        num[i] = (_nll(pp, *args)[0] - _nll(pm, *args)[0]) / (2 * h)

    rel = np.abs(grad - num) / np.maximum(np.abs(num), 1.0)
    log.info("errore relativo massimo del gradiente: %.3e", rel.max())
    assert rel.max() < 1e-6, f"gradiente sbagliato: errore relativo {rel.max():.2e}"

    # Che l'ottimizzatore trovi davvero un punto stazionario, non solo che il
    # gradiente sia coerente con se stesso.
    res = minimize(_nll, p, jac=True, method="L-BFGS-B",
                   bounds=[(None, None)] * (2 * n_t + 2) + [(-0.4, 0.1)], args=args)
    log.info("convergenza: %s, norma del gradiente finale %.2e",
             res.success, np.abs(res.jac).max())
    assert res.success, "l'ottimizzatore non converge"
    log.info("controlli su Dixon-Coles superati")


def main() -> None:
    ap = argparse.ArgumentParser(description="Dixon-Coles con decadimento temporale")
    ap.add_argument("--check", action="store_true", help="verifica il gradiente analitico")
    ap.add_argument("--tune", action="store_true", help="tara la half-life sulla validazione")
    args = ap.parse_args()

    if args.check:
        _check_gradient()
        return
    if args.tune:
        from ..evaluate import load_dataset
        print(tune_halflife(load_dataset()).to_string(index=False))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
