"""Alembic environment - the reviewable prod migration path for the `runs`
table. `forge_api.db.init_db()` (create_all) is the dev/test convenience path;
run `alembic upgrade head` against Postgres in staging/prod instead."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from forge_api import models_orm  # noqa: F401  (registers metadata)
from forge_api.config import get_settings
from forge_api.db import Base
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_async_engine(_url())

    async def do_run() -> None:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync_migrations)

    def _run_sync_migrations(connection) -> None:  # noqa: ANN001
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

    asyncio.run(do_run())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
