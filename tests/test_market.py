"""
Il de-vigging di Shin fa quello che deve.

E' il cuore del modello in produzione: M1 e' esattamente questa trasformazione.
Se sbaglia, sbaglia tutto, e sbaglia in silenzio — perche' produce comunque tre
numeri che sommano a uno e sembrano probabilita'.

Uso:
    python -m tests.test_market
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.market import (  # noqa: E402
    devig_proportional, devig_shin, implied_total_goals, implied_lambdas,
)


def _quote_plausibili(n: int, seed: int) -> np.ndarray:
    """
    Quote decimali realistiche: nessuna probabilita' oltre il 90%, margine
    fra il 3% e il 9%. Serve un generatore che produca quote POSSIBILI —
    dirichlet a bassa concentrazione arriva al 97% e con il margine genera
    quote sotto 1, che non esistono.
    """
    rng = np.random.default_rng(seed)
    p = rng.dirichlet([3.0, 3.0, 3.0], size=n)
    p = np.clip(p, 0.03, 0.90)
    p = p / p.sum(axis=1, keepdims=True)
    quote = 1.0 / (p * rng.uniform(1.03, 1.09, size=(n, 1)))
    assert (quote > 1.0).all(), "il generatore ha prodotto quote impossibili"
    return quote


def _shin_riferimento(quote):
    """Implementazione scalare indipendente, con un risolutore diverso."""
    pi = 1.0 / np.asarray(quote, dtype=float)
    tot = pi.sum()

    def somma(z):
        return ((np.sqrt(z**2 + 4 * (1 - z) * pi**2 / tot) - z) / (2 * (1 - z))).sum() - 1

    z = brentq(somma, 0.0, 0.99, xtol=1e-14)
    return (np.sqrt(z**2 + 4 * (1 - z) * pi**2 / tot) - z) / (2 * (1 - z)), z


def test_somma_e_dominio() -> None:
    quote = _quote_plausibili(500, seed=0)

    ps, z = devig_shin(quote)
    assert np.isfinite(ps).all(), "probabilita' non finite"
    assert np.allclose(ps.sum(axis=1), 1.0, atol=1e-12), "non sommano a 1"
    assert (ps > 0).all(), "probabilita' non positive"
    assert (z >= 0).all() and (z < 0.5).all(), f"z fuori scala: {z.min()}, {z.max()}"
    print("1. somma a 1, positive, z in scala                   ok")


def test_contro_risolutore_indipendente() -> None:
    quote = _quote_plausibili(200, seed=1)
    ps, z = devig_shin(quote)

    err = 0.0
    for i in range(len(quote)):
        rif, z_rif = _shin_riferimento(quote[i])
        err = max(err, np.abs(rif - ps[i]).max())
    print(f"2. contro brentq scalare: scarto max {err:.2e}          ok")
    assert err < 1e-12, f"la bisezione vettoriale diverge dal riferimento: {err:.2e}"


def test_firma_del_metodo() -> None:
    """
    Shin carica il margine sugli esiti improbabili: rispetto al proporzionale
    alza il favorito e abbassa lo sfavorito. E' cio' che lo distingue, e se
    sparisce vuol dire che si sta normalizzando e basta.
    """
    quote = _quote_plausibili(800, seed=2)
    ps = devig_shin(quote)[0]
    pp = devig_proportional(quote)
    assert np.isfinite(ps).all(), "quote di prova non valide: il generatore sbaglia"
    r = np.arange(len(ps))
    fav, sfav = ps.argmax(axis=1), ps.argmin(axis=1)

    assert (ps[r, fav] >= pp[r, fav] - 1e-12).all(), "Shin non alza il favorito"
    assert (ps[r, sfav] <= pp[r, sfav] + 1e-12).all(), "Shin non abbassa lo sfavorito"
    # e il margine tolto e' tutto quello che c'era
    assert np.allclose(pp.sum(axis=1), 1.0), "il proporzionale non normalizza"
    print("3. favorito su, sfavorito giu' rispetto al proporzionale  ok")


def test_quote_impossibili() -> None:
    """
    Una quota <= 1 non e' un'offerta, e' un errore di dato: deve produrre NaN,
    non una probabilita' plausibile.

    Questo controllo nasce da un difetto del test stesso: il primo generatore
    di quote sintetiche produceva quote sotto 1, `devig_shin` le rifiutava
    (giustamente) con NaN, e l'assert sulla firma di Shin falliva — perche'
    `NaN >= x` e' False, non perche' il metodo sbagliasse. Un NaN che si
    propaga in un confronto sembra una violazione: meglio verificarlo qui.
    """
    quote = np.array([
        [2.0, 3.5, 4.0],     # valida
        [0.95, 3.5, 4.0],    # quota sotto 1
        [1.0, 3.5, 4.0],     # quota esattamente 1
        [2.0, np.nan, 4.0],  # buco nei dati
    ])
    ps, z = devig_shin(quote)
    assert np.isfinite(ps[0]).all(), "la riga valida doveva essere calcolata"
    assert np.isnan(ps[1:]).all(), "le righe impossibili dovevano dare NaN"
    assert np.isnan(z[1:]).all()
    print("4. quote <= 1 o mancanti danno NaN, non numeri finti  ok")


def test_libro_in_arbitraggio() -> None:
    """Overround <= 1: Shin non ha soluzione, si ricade sul proporzionale."""
    quote = np.array([[3.2, 3.6, 3.4]])       # somma dei reciproci < 1
    assert (1 / quote).sum() < 1.0
    ps, z = devig_shin(quote)
    assert np.allclose(ps.sum(), 1.0), "non somma a 1 nel caso degenere"
    assert z[0] == 0.0, "z dovrebbe essere zero senza margine da togliere"
    assert np.allclose(ps[0], devig_proportional(quote)[0]), \
        "nel caso degenere deve coincidere col proporzionale"
    print("5. overround <= 1 ricade sul proporzionale           ok")


def test_lambda_impliciti() -> None:
    """
    Il giro quote -> probabilita' -> lambda -> probabilita' deve chiudersi.

    Sono i lambda che finiscono in produzione: se il totale implicito o la
    scomposizione sbagliano, sbagliano i gol attesi e tutti i mercati derivati.
    """
    from scipy.stats import poisson

    p_over = np.array([0.40, 0.50, 0.55, 0.65])
    tot = implied_total_goals(p_over)
    # P(over 2.5 | Poisson(tot)) deve tornare il valore di partenza
    assert np.allclose(poisson.sf(2, tot), p_over, atol=1e-8), "il totale non si inverte"
    assert (np.diff(tot) > 0).all(), "il totale deve crescere con la P(over)"
    print(f"6. totale implicito invertibile (lambda {tot.round(2)})   ok")

    p_home = np.array([0.30, 0.45, 0.60, 0.75])
    lam_h, lam_a = implied_lambdas(p_home, tot)
    assert np.isfinite(lam_h).all() and np.isfinite(lam_a).all()
    assert (lam_h > 0).all() and (lam_a > 0).all(), "lambda non positivi"
    assert np.allclose(lam_h + lam_a, tot, atol=1e-6), \
        "la scomposizione non conserva il totale"

    # e la P(1) ricostruita dai due lambda deve tornare quella di partenza
    from src.models.baseline import score_matrix, outcomes_from_matrix
    rico = outcomes_from_matrix(score_matrix(lam_h, lam_a))["p_home"].to_numpy()
    assert np.allclose(rico, p_home, atol=1e-4), \
        f"la P(1) non si ricostruisce: {rico.round(4)} contro {p_home}"
    print("7. scomposizione in lambda coerente con la P(1)      ok")


def main() -> None:
    test_somma_e_dominio()
    test_contro_risolutore_indipendente()
    test_firma_del_metodo()
    test_quote_impossibili()
    test_libro_in_arbitraggio()
    test_lambda_impliciti()
    print("\ntutti i controlli sul de-vigging superati")


if __name__ == "__main__":
    main()
