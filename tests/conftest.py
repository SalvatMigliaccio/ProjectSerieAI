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


def blocchi_mancanti() -> list[str]:
    """
    Quali blocchi di feature non sono stati costruiti.

    PERCHE' NON BASTA `dati_presenti()`. Quella dice che `matches_master` c'e',
    e per quasi tutti i test e' abbastanza. Ma alcuni confrontano l'INSIEME
    delle colonne del dataset con il registro dei set, e quelli hanno bisogno
    che ogni blocco sia stato costruito: con un blocco in meno falliscono su
    una differenza di colonne che sembra una regressione del registro e non e'
    altro che un parquet non ancora prodotto.

    E' successo: il blocco GIOCATORI e' stato rinviato (serve ~7 ore di
    WhoScored, vedi docs/ROADMAP.md) e due test sono diventati rossi senza che
    niente fosse rotto. Un rosso che non corrisponde a un difetto e' peggio di
    un test saltato: dopo due giorni nessuno lo guarda piu'.
    """
    if not dati_presenti():
        return []
    from goalmodel.features import registry
    return [b.nome for b in registry.mancanti()]


def _database_reachable() -> bool:
    """
    Is the auth database up and migrated?

    Checked once and cached. The alternative is every database test failing
    with a connection error, which reads like a broken test suite rather than
    "the containers are not running".
    """
    global _DB_OK
    if _DB_OK is None:
        try:
            from backend.auth.db import engine
            from sqlalchemy import text
            with engine().connect() as c:
                c.execute(text("SELECT 1 FROM roles LIMIT 1"))
            _DB_OK = True
        except Exception:
            _DB_OK = False
    return _DB_OK


_DB_OK: bool | None = None


@pytest.fixture
def db():
    """
    A session inside a transaction that is always rolled back.

    Tests share one migrated database and must not see each other's rows.
    Binding the session to an open connection-level transaction and rolling it
    back afterwards is cheaper and more reliable than deleting rows: nothing
    survives, not even on failure.
    """
    from backend.auth.db import engine
    from sqlalchemy.orm import Session as SASession

    conn = engine().connect()
    trans = conn.begin()
    s = SASession(bind=conn, expire_on_commit=False)
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


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
def pytest_collection_modifyitems(config, items: list[pytest.Item]) -> None:
    """Salta i test che chiedono data/ quando data/ non c'e'."""
    if not dati_presenti():
        salta = pytest.mark.skip(
            reason=f"serve {DATASET.name}: lancia l'ingestion (docs/COMANDI.md sezione 2)"
        )
        for item in items:
            if "richiede_dati" in item.keywords:
                item.add_marker(salta)
        return

    if not _database_reachable():
        salta_db = pytest.mark.skip(
            reason="Postgres not reachable or not migrated: "
                   "docker compose up -d && alembic -c alembic.ini upgrade head"
        )
        for item in items:
            if "requires_db" in item.keywords:
                item.add_marker(salta_db)

    mancanti = blocchi_mancanti()
    if mancanti:
        salta = pytest.mark.skip(
            reason=f"blocchi non costruiti: {', '.join(mancanti)}. "
                   f"Il dataset c'e' ma e' incompleto (docs/ROADMAP.md)"
        )
        for item in items:
            if "richiede_dataset_completo" in item.keywords:
                item.add_marker(salta)
