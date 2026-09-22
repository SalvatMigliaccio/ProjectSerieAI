"""
La sezione 3 del report esce anche quando il dataset ha piu' colonne delle
partite in arrivo — il difetto B1.

PERCHE' ESISTE. Il modello della sezione si addestra su `load_dataset()`, che
unisce i parquet di tutti i blocchi, e predice su quello che
`predict.build_features()` sa ricostruire per una partita non ancora giocata.
Il secondo insieme e' per forza piu' piccolo: le assenze del turno in arrivo
non sono ancora state scaricate. Finche' il modello si prendeva tutte le
colonne del dataset, la sezione veniva SALTATA con un avviso — una
funzionalita' persa senza che niente fallisse.

Il test finge esattamente quello squilibrio: una colonna che c'e' nello
storico e non nelle partite da predire. Se qualcuno rimettesse il modello a
scegliere le feature dal solo dataset, qui la tabella tornerebbe vuota.

    python -m tests.test_divergenza
"""

import numpy as np
import pandas as pd

from goalmodel.evaluation import evaluate
from goalmodel.reporting import report

rng = np.random.default_rng(7)

# Sta nel dataset e NON nelle partite in arrivo: e' il nome vero della prima
# feature del blocco giocatori, quella su cui LightGBM manderebbe ogni riga
# futura sul ramo dei mancanti.
SOLO_STORICO = "home_quota_minuti_assenti"


def _storico(n: int = 1200) -> pd.DataFrame:
    """Storico sintetico con una forza di squadra che i gol seguono davvero."""
    forza = rng.normal(0, 0.35, n)
    return pd.DataFrame({
        "league": "ITA-Serie A",
        "season": "2425",
        "home_team": [f"H{i % 20}" for i in range(n)],
        "away_team": [f"A{i % 19}" for i in range(n)],
        "date": pd.date_range("2020-01-01", periods=n, freq="D"),
        "home_form": forza,
        "away_form": rng.normal(0, 0.35, n),
        SOLO_STORICO: rng.uniform(0, 0.3, n),
        "FTHG": rng.poisson(np.exp(0.3 + forza)).astype(float),
        "FTAG": rng.poisson(1.1, n).astype(float),
        "FTR": "H",
    })


def _in_arrivo(storico: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Partite da predire: le stesse colonne MENO quella che non si sa."""
    p = rng.dirichlet([4.0, 3.0, 3.0], n)
    return pd.DataFrame({
        "home_team": [f"H{i}" for i in range(n)],
        "away_team": [f"A{i}" for i in range(n)],
        "kickoff": pd.Timestamp("2026-09-26", tz="UTC") + pd.to_timedelta(range(n), "h"),
        "date": storico["date"].max() + pd.Timedelta(days=7),
        "home_form": rng.normal(0, 0.35, n),
        "away_form": rng.normal(0, 0.35, n),
        "p_home": p[:, 0], "p_draw": p[:, 1], "p_away": p[:, 2],
    })


def test_colonna_in_piu_nel_dataset() -> None:
    storico = _storico()
    preds = _in_arrivo(storico)
    assert SOLO_STORICO not in preds.columns

    vero = evaluate.load_dataset
    try:
        evaluate.load_dataset = lambda *a, **k: storico
        diag = report.Diagnostica()
        tab = report.divergenza(preds, diag)
    finally:
        evaluate.load_dataset = vero

    assert not tab.empty, (
        "sezione 3 saltata: il modello si e' addestrato su colonne che le "
        "partite in arrivo non hanno (B1)"
    )
    assert not diag.avvisi, f"la sezione ha avvisato invece di uscire: {diag.avvisi}"
    assert len(tab) == len(preds)
    # Un modello degenere prevede la stessa cosa per tutte: lo scarto sarebbe
    # identico riga per riga e la sezione non direbbe niente.
    assert tab["scarto"].std() > 0, "M4 ha previsto la stessa cosa ovunque"
    print(f"  colonna solo nello storico: sezione prodotta, {len(tab)} righe   ok")


def main() -> None:
    test_colonna_in_piu_nel_dataset()
    print("\ncontrolli sulla divergenza superati")


if __name__ == "__main__":
    main()
