"""
Il registro delle previsioni e' append-only e idempotente insieme.

E' la garanzia su cui poggia tutto il track record: se rilanciare il ciclo
raddoppiasse le righe, l'RPS cumulativo sarebbe calcolato su previsioni
duplicate; se una rilettura sovrascrivesse una riga vecchia, il track record
verrebbe falsificato a posteriori con informazione che al momento della
previsione non c'era.

Non tocca la rete ne' i dati veri: lavora su un file temporaneo.

Uso:
    python -m tests.test_predictions_log
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.predict import LOG_COLUMNS, already_logged, append_log  # noqa: E402

MODELLO = "M1 market-only (lambda)"
ALTRO = "M7 sperimentale"


def _finte(n: int = 3, quote: float = 2.0) -> pd.DataFrame:
    """Previsioni sintetiche con la forma che append_log si aspetta."""
    return pd.DataFrame({
        "league": ["ITA-Serie A"] * n,
        "season": ["2627"] * n,
        "home_team": [f"Casa{i}" for i in range(n)],
        "away_team": [f"Fuori{i}" for i in range(n)],
        "matchday": [3] * n,
        "date": pd.to_datetime(["2026-09-06"] * n),
        "lambda_home": np.linspace(1.0, 2.0, n),
        "lambda_away": np.linspace(1.5, 0.8, n),
        "p_home": np.full(n, 0.45),
        "p_draw": np.full(n, 0.27),
        "p_away": np.full(n, 0.28),
        "p_over25": np.full(n, 0.52),
        "p_btts": np.full(n, 0.51),
        "odds_home": np.full(n, quote),
        "odds_draw": np.full(n, 3.4),
        "odds_away": np.full(n, 3.6),
        "odds_source": ["B365-fixtures"] * n,
    })


def _righe(path: Path) -> int:
    return 0 if not path.exists() else len(pd.read_csv(path))


def main() -> None:
    ora = pd.Timestamp("2026-09-05T10:00:00Z")
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "predictions_log.csv"

        # 1. prima scrittura
        scritte, saltate = append_log(_finte(), MODELLO, ora, path=log)
        assert (scritte, saltate) == (3, 0), (scritte, saltate)
        assert _righe(log) == 3
        assert list(pd.read_csv(log).columns) == LOG_COLUMNS, "schema del registro cambiato"
        print("1. prima scrittura: 3 righe                          ok")

        # 2. IDEMPOTENZA: rilanciare non aggiunge niente
        scritte, saltate = append_log(_finte(), MODELLO, ora, path=log)
        assert (scritte, saltate) == (0, 3), (scritte, saltate)
        assert _righe(log) == 3, "il rilancio ha duplicato le righe"
        print("2. rilancio identico: 0 scritte, 3 saltate           ok")

        # 3. quote cambiate: la riga vecchia NON si aggiorna
        scritte, saltate = append_log(_finte(quote=1.5), MODELLO, ora, path=log)
        assert (scritte, saltate) == (0, 3), (scritte, saltate)
        letto = pd.read_csv(log)
        assert (letto["odds_home"] == 2.0).all(), "una quota vecchia e' stata sovrascritta"
        print("3. quote cambiate: registro intatto                  ok")

        # 4. stesso match, modello diverso: entra
        scritte, saltate = append_log(_finte(quote=1.5), ALTRO, ora, path=log)
        assert (scritte, saltate) == (3, 0), (scritte, saltate)
        assert _righe(log) == 6
        print("4. altro model_version: 3 righe nuove                ok")

        # 5. misto: due gia' viste, una nuova
        misto = _finte(4)
        scritte, saltate = append_log(misto, MODELLO, ora, path=log)
        assert (scritte, saltate) == (1, 3), (scritte, saltate)
        assert _righe(log) == 7
        print("5. tre viste + una nuova: 1 scritta, 3 saltate       ok")

        # 6. append-only: nessuna riga precedente e' cambiata
        finale = pd.read_csv(log)
        primi = finale.head(3)
        assert (primi["model_version"] == MODELLO).all()
        assert (primi["odds_home"] == 2.0).all()
        assert finale["timestamp_prediction"].nunique() == 1
        print("6. le righe iniziali sono intatte                    ok")

        # 7. already_logged distingue i modelli
        assert len(already_logged(MODELLO, log)) == 4
        assert len(already_logged(ALTRO, log)) == 3
        assert already_logged("mai visto", log) == set()
        print("7. already_logged separa i model_version             ok")

    print("\ntutti i controlli sul registro superati")


if __name__ == "__main__":
    main()
