"""
SQLAlchemy engine and session.

WHY A POOL AND NOT A CONNECTION PER REQUEST. Opening a Postgres connection
costs a handshake of a few milliseconds; per request that is paid on every
call. The pool reuses them.

`pool_pre_ping` is not pessimism: a restarted database, or a firewall closing
idle connections, leaves sockets in the pool that look alive and fail on the
first query. The ping costs a trivial round trip and turns a random error into
a reconnect.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings

log = logging.getLogger("auth.db")


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(
        settings().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        future=True,
    )


@lru_cache(maxsize=1)
def session_factory() -> sessionmaker[Session]:
    # `expire_on_commit=False`: objects stay readable after commit. With the
    # default, touching an attribute post-commit fires a fresh query, and
    # inside a FastAPI dependency that query lands outside the session and
    # raises `DetachedInstanceError` somewhere far away.
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on clean exit, rollback on exception."""
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def request_session() -> Iterator[Session]:
    """FastAPI dependency. Same semantics, the shape `Depends` expects."""
    with session_scope() as s:
        yield s
