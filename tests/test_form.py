"""
Test del modulo form.py.

Il piu' importante e' test_no_leakage: verifica che le feature di una partita
non contengano informazione della partita stessa. E' la difesa automatica
contro l'errore che rovina silenziosamente questo tipo di progetti.

    python -m tests.test_form
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.features import form  # noqa: E402

rng = np.random.default_rng(7)

TEAMS = ["Napoli", "Inter", "Milan", "Juventus", "Roma", "Lazio"]
SEASONS = ["2324", "2425", "2526"]


def synthetic() -> pd.DataFrame:
    """Dati con i nomi di colonna REALI del progetto."""
    rows = []
    day = datetime(2023, 8, 20)
    for season in SEASONS:
        for i, home in enumerate(TEAMS):
            for away in TEAMS:
                if home == away:
                    continue
                day += timedelta(hours=30)
                hg, ag = int(rng.poisson(1.6)), int(rng.poisson(1.2))
                rows.append({
                    "league": "ITA-Serie A", "season": season, "date": day,
                    "home_team": home, "away_team": away,
                    "FTHG": hg, "FTAG": ag,
                    "HS": int(rng.poisson(13)), "AS": int(rng.poisson(11)),
                    "HST": int(rng.poisson(5)), "AST": int(rng.poisson(4)),
                    "home_np_xg": rng.uniform(0.3, 3.0),
                    "away_np_xg": rng.uniform(0.2, 2.6),
                    "home_ppda": rng.uniform(6, 20),
                    "away_ppda": rng.uniform(6, 20),
                    "home_deep_completions": int(rng.poisson(8)),
                    "away_deep_completions": int(rng.poisson(6)),
                    "home_expected_points": rng.uniform(0, 3),
                    "away_expected_points": rng.uniform(0, 3),
                    "home_points": 3 if hg > ag else (1 if hg == ag else 0),
                    "away_points": 3 if ag > hg else (1 if hg == ag else 0),
                })
    return pd.DataFrame(rows)


def test_no_leakage(df: pd.DataFrame, wide: pd.DataFrame) -> None:
    """
    Ricalcola a mano la media mobile di una squadra usando SOLO le partite
    con data strettamente precedente, e la confronta con quella prodotta
    dal modulo. Se il modulo usasse la partita corrente, i due numeri
    divergerebbero.
    """
    alpha = 1 - 0.5 ** (1 / 6)
    team = "Napoli"

    long = form.to_long(df)
    tl = long[long.team == team].sort_values("date").reset_index(drop=True)

    # Stato manuale, senza attenuazione di stagione: si controlla quindi
    # solo la prima stagione, dove l'attenuazione non e' ancora intervenuta.
    first_season = tl.season.iloc[0]
    tl = tl[tl.season == first_season].reset_index(drop=True)

    manual, state = [], None
    for v in tl["np_xg_for"]:
        manual.append(state)
        state = v if state is None else state + alpha * (v - state)

    got = []
    for _, r in tl.iterrows():
        m = wide[(wide.home_team == r.home_team) & (wide.away_team == r.away_team)
                 & (wide.season == r.season)]
        col = "home_np_xg_for_ewm" if r.venue == "home" else "away_np_xg_for_ewm"
        got.append(m[col].iloc[0])

    for i, (exp, act) in enumerate(zip(manual, got)):
        if exp is None:
            assert pd.isna(act), f"riga {i}: attesa NaN, ottenuto {act}"
        else:
            assert abs(exp - act) < 1e-9, f"riga {i}: atteso {exp:.6f}, ottenuto {act:.6f}"

    print(f"  test_no_leakage             OK  ({len(manual)} partite verificate)")


def test_first_match_is_nan(wide: pd.DataFrame) -> None:
    """La primissima partita di una squadra non puo' avere storico."""
    first = wide.sort_values("date").iloc[0]
    assert pd.isna(first["home_np_xg_for_ewm"]), "prima partita con storico: impossibile"
    assert pd.isna(first["away_np_xg_for_ewm"]), "prima partita con storico: impossibile"
    print("  test_first_match_is_nan     OK")


def test_counters(wide: pd.DataFrame) -> None:
    """I contatori partite devono partire da zero e crescere."""
    assert wide["home_matches_total"].min() == 0
    assert wide["home_matches_season"].min() == 0
    assert wide["home_matches_total"].max() >= wide["home_matches_season"].max()
    print("  test_counters               OK")


def test_no_future_rows(df: pd.DataFrame, wide: pd.DataFrame) -> None:
    """Il numero di righe deve restare identico all'ingresso."""
    assert len(wide) == len(df), f"{len(df)} -> {len(wide)}"
    print(f"  test_no_future_rows         OK  ({len(wide)} righe)")


if __name__ == "__main__":
    df = synthetic()
    print(f"dati sintetici: {len(df)} partite, {df.season.nunique()} stagioni\n")

    # save=False non e' un dettaglio: con il default il test scriverebbe le
    # sue 90 righe sintetiche sopra `data/processed/features_form.parquet`,
    # cioe' sopra le 4580 righe vere. Non darebbe nessun errore — il merge di
    # `evaluate.load_dataset` riempirebbe di NaN tutte le feature di forma e
    # M4 degenererebbe in un modello costante senza protestare. E' successo.
    wide = form.build(df, save=False)
    print()

    test_no_future_rows(df, wide)
    test_counters(wide)
    test_first_match_is_nan(wide)
    test_no_leakage(df, wide)

    print("\nesempio di output:")
    cols = ["season", "home_team", "away_team", "home_np_xg_for_ewm",
            "away_np_xg_for_ewm", "diff_np_xg_for_ewm", "home_matches_total"]
    print(wide[cols].tail(6).to_string(index=False))