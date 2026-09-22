"""
Configurazione comune dei test.

DUE PROBLEMI, DUE RISPOSTE.

1. I test nascono come script (`python -m tests.test_x`) e alcune funzioni
   ricevono i dati come argomento da un `main()` scritto a mano. pytest legge
   quegli argomenti come fixture e fallisce in fase di setup. Le fixture
   condivise stanno qui; quelle specifiche di un modulo stanno nel modulo.

2. Alcuni test hanno bisogno di `data/`, che non e' versionata e in un clone
   pulito non esiste. Marcarli `richiede_dati` li fa SALTARE con un messaggio,
   invece di farli fallire con un FileNotFoundError che sembra una regressione.
   E' la differenza fra "questo test non puo' girare qui" e "questo test e'
   rotto": in CI, dove data/ non c'e' mai, la distinzione e' tutto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from goalmodel import config

DATASET = config.INTERIM / "matches_master.parquet"


def dati_presenti() -> bool:
    """C'e' un dataset vero su cui girare?"""
    return DATASET.exists()


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """
    Alias di `tmp_path`.

    I test del ciclo della giornata sostituiscono il registro e l'archivio
    veri con file temporanei, e chiamano quel parametro `tmp`.
    """
    return tmp_path


# pytest impone il nome `config` per questo parametro, che qui coprirebbe il
# modulo `goalmodel.config` importato sopra. Il percorso del dataset e' gia'
# risolto in DATASET, quindi dentro l'hook il modulo non serve.
def pytest_collection_modifyitems(config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    """Salta i test che chiedono data/ quando data/ non c'e'."""
    if dati_presenti():
        return
    salta = pytest.mark.skip(
        reason=f"serve {DATASET.name}: lancia l'ingestion (docs/COMANDI.md sezione 2)"
    )
    for item in items:
        if "richiede_dati" in item.keywords:
            item.add_marker(salta)
