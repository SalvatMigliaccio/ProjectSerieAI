"""
Il motore e la sessione SQLAlchemy.

PERCHE' UN POOL E NON UNA CONNESSIONE PER RICHIESTA. Aprire una connessione a
Postgres costa una manciata di millisecondi di handshake: per richiesta e'
tempo speso a ogni chiamata. Il pool le riusa.

`pool_pre_ping` non e' pessimismo: un database riavviato, o un firewall che
chiude le connessioni inattive, lascia nel pool socket che sembrano vivi e
falliscono alla prima query. Il ping costa una query banale e trasforma un
errore casuale in una riconnessione.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import impostazioni

log = logging.getLogger("auth.db")


@lru_cache(maxsize=1)
def motore() -> Engine:
    cfg = impostazioni()
    return create_engine(
        cfg.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        # Gli identificatori finiscono nei log di Postgres: un URL con la
        # password dentro non deve comparirci.
        hide_parameters=False,
        future=True,
    )


@lru_cache(maxsize=1)
def fabbrica_sessioni() -> sessionmaker[Session]:
    # `expire_on_commit=False`: dopo il commit gli oggetti restano leggibili.
    # Con il default, accedere a un attributo dopo il commit scatena una query
    # nuova — e in una dipendenza FastAPI quella query cade fuori dalla
    # sessione, sollevando `DetachedInstanceError` in un punto lontano.
    return sessionmaker(bind=motore(), expire_on_commit=False, future=True)


@contextmanager
def sessione() -> Iterator[Session]:
    """Una transazione: commit se esce bene, rollback se solleva."""
    s = fabbrica_sessioni()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def sessione_richiesta() -> Iterator[Session]:
    """Dipendenza FastAPI. Stessa semantica, forma che Depends si aspetta."""
    with sessione() as s:
        yield s
