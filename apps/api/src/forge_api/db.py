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


async def init_db() -> None:
    from forge_api import models_orm  # noqa: F401  (registers metadata)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    return _session_factory
