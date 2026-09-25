"""
Alembic context.

TWO THINGS THIS FILE DOES THAT THE GENERATED ONE DOES NOT.

1. **The URL comes from settings, not from alembic.ini.** A versioned file
   holding the database password is the most common way to break security
   rule 1.

2. **Tables that are not ours are ignored.** Other things will live in this
   database (ADR 0002 brings the match data, pgvector has its own). Without a
   filter, `--autogenerate` proposes DROPPING everything not declared here: a
   migration that looks right and deletes a table nobody registered.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from backend.auth.models import Base
from backend.auth.settings import settings
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings().database_url)
target_metadata = Base.metadata

OURS = set(Base.metadata.tables)


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """Only the tables declared here. The rest of the database is not ours."""
    if type_ == "table":
        return name in OURS
    return True


def _run(connection=None) -> None:
    context.configure(
        connection=connection,
        url=None if connection is not None else config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=connection is None,
        include_name=include_name,
        # Without this, a VARCHAR(16) widened to VARCHAR(32) goes unnoticed:
        # the migration passes and the column stays short.
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    _run()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as conn:
        _run(conn)
