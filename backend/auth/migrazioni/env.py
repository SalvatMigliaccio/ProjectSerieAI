"""
Il contesto di Alembic.

DUE COSE CHE QUESTO FILE FA E CHE QUELLO GENERATO NON FA.

1. **L'URL arriva dalle impostazioni, non da alembic.ini.** Un file versionato
   con dentro la password del database e' il modo piu' comune di violare la
   regola di sicurezza n.1.

2. **Le tabelle che non sono nostre vengono ignorate.** Nello stesso database
   vivranno altre cose (ADR 0002 ci portera' i dati di partita, e pgvector ha
   le sue). Senza un filtro, `--autogenerate` propone di CANCELLARE tutto cio'
   che non e' nei modelli: una migrazione che sembra corretta e butta via una
   tabella che nessuno aveva dichiarato qui.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.auth.models import Base
from backend.auth.settings import impostazioni

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", impostazioni().database_url)
target_metadata = Base.metadata

NOSTRE = set(Base.metadata.tables)


def includi(name: str | None, type_: str, parent_names: dict) -> bool:
    """Solo le tabelle dichiarate qui. Il resto del database non ci riguarda."""
    if type_ == "table":
        return name in NOSTRE
    return True


def _esegui(connection=None) -> None:
    context.configure(
        connection=connection,
        url=None if connection is not None else config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=connection is None,
        include_name=includi,
        # Senza questo, un VARCHAR(16) portato a VARCHAR(32) non viene notato:
        # la migrazione passa e la colonna resta corta.
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    _esegui()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as conn:
        _esegui(conn)
