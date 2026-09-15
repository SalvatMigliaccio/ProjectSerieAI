"""
Forma per sede: la forma casalinga non vede le trasferte, e nessuna riga vede
se stessa. Dati sintetici, nessuna scrittura.

Uso:
    python -m tests.test_forma_venue
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.experiments.forma_venue import costruisci
from src.features import sets as sets_mod

KEYS = ["league", "season", "home_team", "away_team"]


def _partite() -> pd.DataFrame:
    righe = [
        # data,        casa, fuori, gol casa, gol fuori
        ("2020-09-01", "A", "B", 3, 0),
        ("2020-09-08", "B", "A", 1, 5),   # A segna 5 IN TRASFERTA
        ("2020-09-15", "A", "C", 0, 0),
        ("2020-09-22", "C", "A", 2, 2),
    ]
    df = pd.DataFrame(righe, columns=["date", "home_team", "away_team", "FTHG", "FTAG"])
    df["league"], df["season"] = "ITA", "2021"
    df["date"] = pd.to_datetime(df["date"])
    return df


def _riga(out: pd.DataFrame, casa: str, fuori: str) -> pd.Series:
    return out[(out["home_team"] == casa) & (out["away_team"] == fuori)].iloc[0]


def test_la_casa_non_vede_le_trasferte() -> None:
    out = costruisci(_partite(), halflife=10)
    # A-C: A ha giocato in casa solo A-B (3 gol). I 5 gol di B-A sono in trasferta.
    assert _riga(out, "A", "C")["home_goals_for_ewm_sede"] == 3.0
    # C-A: A in trasferta ha solo B-A alle spalle.
    assert _riga(out, "C", "A")["away_goals_for_ewm_sede"] == 5.0


def test_nessuna_riga_vede_se_stessa() -> None:
    out = costruisci(_partite(), halflife=10)
    # Prima partita in casa di A e prima in trasferta di A: nessuno storico.
    assert np.isnan(_riga(out, "A", "B")["home_goals_for_ewm_sede"])
    assert np.isnan(_riga(out, "B", "A")["away_goals_for_ewm_sede"])
    # B-A e' la prima di B in casa, anche se B ha gia' giocato (in trasferta).
    assert np.isnan(_riga(out, "B", "A")["home_goals_for_ewm_sede"])


def test_differenza_e_nomi() -> None:
    out = costruisci(_partite(), halflife=10)
    r = _riga(out, "C", "A")
    assert np.isnan(r["diff_goals_for_ewm_sede"])  # C non ha partite in casa prima
    registrate = set(sets_mod.SETS["FORMA_VENUE"].colonne)
    prodotte = {c for c in out.columns if c not in KEYS}
    assert prodotte <= registrate, prodotte - registrate


def main() -> None:
    for t in (test_la_casa_non_vede_le_trasferte, test_nessuna_riga_vede_se_stessa,
              test_differenza_e_nomi):
        t()
        print(f"  {t.__name__:<40} ok")
    print("\nforma per sede: tutti i controlli superati")


if __name__ == "__main__":
    main()
