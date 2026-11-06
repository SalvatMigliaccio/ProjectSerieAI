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
    # `create_savepoint` is what lets the code under test COMMIT. Production
    # commits on the way out of every request and on a refused sign-in, so a
    # harness where commit is impossible cannot reproduce production - and did
    # not: the transaction ended up deassociated from the connection and the
    # rows survived the rollback. With a savepoint the session's commit reaches
    # only as far as that savepoint, and `trans.rollback()` below still
    # discards everything.
    s = SASession(bind=conn, expire_on_commit=False,
                  join_transaction_mode="create_savepoint")
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


@pytest.fixture(autouse=True)
def _limitatori_azzerati():
    """
    Ogni limitatore di richieste parte vuoto a ogni test.

    Vivono nella memoria del processo e sono condivisi: senza questo il
    limite sull'intera API (300 al minuto per indirizzo, e nei test
    l'indirizzo e' sempre lo stesso) scatterebbe a meta' suite con un 429
    che sembra un endpoint rotto. Import pigro: i test del modello girano
    anche senza l'extra `auth`.
    """
    try:
        from backend.auth.ratelimit import ALL_LIMITERS
    except ImportError:
        yield
        return
    for limiter in ALL_LIMITERS:
        limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _dati_veri_intoccabili(monkeypatch):
    """
    Nessun test scrive nei dati veri, ne' su file ne' in Postgres.

    PERCHE' UNA RETE E NON UNA CONVENZIONE. Lo e' stata, ed e' gia' caduta due
    volte: `test_form` sovrascriveva `features_form.parquet` con 90 righe
    sintetiche, e con il passaggio a `storage` (ADR 0006) quattro test che
    dirottavano `ingest.RAW` avrebbero scritto nel `data/raw` vero, perche'
    l'ingestion non guardava piu' quella variabile. Nessuno dei due casi da'
    un errore: i dati restano plausibili e sbagliati.

    Due cose: la modalita' e' sempre `parquet`, qualunque cosa dica
    l'ambiente di chi lancia i test — una suite che gira con
    `GOALMODEL_STORAGE=postgres` scriverebbe nel database vero; e ogni
    `storage.scrivi` che cadrebbe dentro il `data/` vero fallisce. Chi deve
    scrivere sposta prima `storage.CARTELLE` su una cartella temporanea.
    """
    from goalmodel import pg, storage

    monkeypatch.setenv(storage.VARIABILE, "parquet")
    # E in Postgres, schemi `test_*`: i test che ci scrivono non vedono e non
    # toccano `raw`, `interim`, `processed`.
    monkeypatch.setenv(pg.VARIABILE_PREFISSO, "test_")
    # Le guardie dell'API e degli esperimenti chiudono `storage` per tutto il
    # processo, e pytest e' un processo solo: senza riaprirlo, ogni test dopo
    # il primo che importa l'API non potrebbe scrivere nemmeno in tmp_path.
    # Chi verifica una guardia la invoca nel test, e la guardia richiude.
    monkeypatch.setattr(storage, "_vietato", None)
    vera = config.DATA.resolve()
    originale = storage.scrivi

    def scrivi(livello: str, nome: str, df) -> None:
        dst = storage.percorso(livello, nome).resolve()
        if dst.is_relative_to(vera):
            raise AssertionError(
                f"un test stava per scrivere nei dati veri: {dst}. "
                f"Sposta storage.CARTELLE['{livello}'] su tmp_path.")
        originale(livello, nome, df)

    monkeypatch.setattr(storage, "scrivi", scrivi)
