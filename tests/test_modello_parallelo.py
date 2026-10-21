"""
Il modello parallelo si registra ACCANTO a M1, mai al suo posto.

Quattro garanzie, ognuna con un modo di rompersi che non darebbe errore:
  1. il nome di M1 con rho = 0 non cambia — cambiandolo, ogni previsione
     scritta dal 2026/27 smetterebbe di essere "di produzione" per l'API, che
     filtra su quel nome;
  2. il parallelo usa gli stessi lambda e la stessa ora, e cambia solo le
     probabilita' che il rho tocca (pareggio e gol-gol, non l'over 2.5);
  3. e' idempotente come il resto del registro;
  4. rifiuta una partita gia' iniziata, con un'eccezione e non un assert.

Lavora su un file temporaneo.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goalmodel import config
from goalmodel.models.baseline import MarketOnly
from goalmodel.prediction.predict import Esito, registra_parallelo

ORA = pd.Timestamp("2026-10-23T10:00:00Z")


def _esito(n: int = 3, kickoff: pd.Timestamp = ORA + pd.Timedelta(days=1)) -> Esito:
    righe = pd.DataFrame({
        "league": ["ITA-Serie A"] * n,
        "season": ["2627"] * n,
        "home_team": [f"Casa{i}" for i in range(n)],
        "away_team": [f"Fuori{i}" for i in range(n)],
        "matchday": [8] * n,
        "date": pd.to_datetime(["2026-10-24"] * n),
        "kickoff": [kickoff] * n,
        "mkt_lambda_home": np.linspace(1.0, 2.2, n),
        "mkt_lambda_away": np.linspace(1.4, 0.8, n),
        "odds_home": np.full(n, 2.1),
        "odds_draw": np.full(n, 3.3),
        "odds_away": np.full(n, 3.5),
        "odds_source": ["B365-fixtures"] * n,
    })
    principale = MarketOnly().predict(righe)
    return Esito(matchday=8, preds=pd.concat([righe, principale], axis=1),
                 model_version=MarketOnly().name, now=ORA)


def test_nome_di_produzione_invariato() -> None:
    assert MarketOnly().name == "M1 market-only (lambda)"
    assert MarketOnly(rho=0.0).name == "M1 market-only (lambda)"
    assert MarketOnly(rho=-0.16).name == "M1 market-only (lambda, rho=-0.16)"


def test_parallelo_stesse_righe_altri_pareggi(tmp_path: Path) -> None:
    registro = tmp_path / "predictions_log.csv"
    esito = _esito()
    modello = MarketOnly(rho=config.M1_RHO_PARALLELO)

    assert registra_parallelo(esito, modello, path=registro) == (3, 0)
    scritto = pd.read_csv(registro)
    assert set(scritto["model_version"]) == {modello.name}
    assert (scritto["timestamp_prediction"] == ORA.strftime("%Y-%m-%dT%H:%M:%SZ")).all()

    base = esito.preds.reset_index(drop=True)
    assert np.allclose(scritto["lambda_home"], base["lambda_home"].round(4))
    assert np.allclose(scritto["lambda_away"], base["lambda_away"].round(4))
    assert np.allclose(scritto["p_over25"], base["p_over25"].round(5)), \
        "il rho non deve toccare l'over 2.5"
    assert (scritto["p_draw"] > base["p_draw"]).all(), "rho negativo deve alzare il pareggio"

    # Idempotenza: rilanciare non aggiunge niente.
    assert registra_parallelo(esito, modello, path=registro) == (0, 3)
    assert len(pd.read_csv(registro)) == 3


def test_parallelo_rifiuta_partite_iniziate(tmp_path: Path) -> None:
    esito = _esito(kickoff=ORA - pd.Timedelta(minutes=1))
    with pytest.raises(ValueError, match="calcio d'inizio"):
        registra_parallelo(esito, MarketOnly(rho=-0.16), path=tmp_path / "log.csv")
    assert not (tmp_path / "log.csv").exists()
