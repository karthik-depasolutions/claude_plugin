"""Async SQLAlchemy engine/session setup. SQLite+aiosqlite by default for
local dev and tests; point `FORGE_DATABASE_URL` at Postgres (`postgresql+psycopg://...`)
in staging/prod — see docker-compose.yml. Table creation here (`init_db`) is
the dev/test path; `migrations/` (Alembic) is the reviewable prod path."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from forge_api.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None):  # noqa: ANN201
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_async_engine(url, connect_args=connect_args)


_engine = make_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def configure(database_url: str) -> None:
    """Rebind the module-level engine/session factory — used by tests to point
    at an isolated SQLite database per test run."""
    global _engine, _session_factory  # noqa: PLW0603 - intentional test seam, see docstring
    _engine = make_engine(database_url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def _sqlite_default_literal(column) -> str | None:  # noqa: ANN001
    """SQLite requires ADD COLUMN's DEFAULT to be a constant, so a column
    whose default is a Python callable (`datetime.now`) can't be backfilled
    this way. Returns None for those - they're added without a default,
    which is only safe because every such column is also nullable."""
    server_default = getattr(column, "server_default", None)
    if server_default is not None and getattr(server_default, "arg", None) is not None:
        return str(server_default.arg)
    default = getattr(column, "default", None)
    if default is None or getattr(default, "is_callable", False):
        return None
    arg = getattr(default, "arg", None)
    if callable(arg) or arg is None:
        return None
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    return "'" + str(arg).replace("'", "''") + "'"


def _add_missing_sqlite_columns(sync_conn) -> None:  # noqa: ANN001
    """Add any column the ORM declares that the live table lacks."""
    from sqlalchemy import text
    from sqlalchemy.schema import CreateColumn

    from forge_api import models_orm  # noqa: F401  (registers metadata)

    dialect = sync_conn.dialect
    for table in Base.metadata.sorted_tables:
        rows = sync_conn.execute(text(f"PRAGMA table_info({table.name})")).fetchall()
        if not rows:
            continue  # table didn't exist; create_all just made it correctly
        existing = {row[1] for row in rows}
        for column in table.columns:
            if column.name in existing:
                continue
            # Compile the real type from the model rather than hardcoding a
            # SQL string, so the added column matches what create_all builds.
            ddl = CreateColumn(column).compile(dialect=dialect).string
            # NOT NULL without a default is rejected on a populated table;
            # the default below supplies the backfill value where there is one.
            ddl = ddl.replace(" NOT NULL", "")
            literal = _sqlite_default_literal(column)
            if literal is not None:
                ddl = f"{ddl} DEFAULT {literal}"
            sync_conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))


async def init_db() -> None:
    from forge_api import models_orm  # noqa: F401  (registers metadata)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # SQLite self-healing auto-migration for dev/test databases.
        #
        # `create_all` only creates *missing tables* - it never alters an
        # existing one - so a dev database made before a column was added
        # keeps the old shape and every query against the new column fails.
        # This used to be a hand-maintained list of ALTER statements, which
        # silently drifted from the Alembic revisions (0005's token columns
        # were missing from it on arrival). Deriving the diff from the ORM
        # metadata instead means adding a column to a model is all it takes;
        # there is no second list to remember.
        #
        # SQLite only, and only for additive changes: Postgres/staging/prod
        # go through `migrations/` (Alembic), which stays the reviewable path.
        if str(_engine.url).startswith("sqlite"):
            await conn.run_sync(_add_missing_sqlite_columns)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    return _session_factory
