from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOKINGS_CSV = REPO_ROOT / "fixtures" / "datasets" / "bookings.csv"


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    db_path = tmp_path / "forge.db"
    runs_dir = tmp_path / "runs"
    monkeypatch.setenv("FORGE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("FORGE_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", "off")
    # A developer's local .env may configure the client warehouse (it's
    # loaded by `Settings(env_file=".env")` regardless of monkeypatch) - the
    # API test suite must stay hermetic and exercise the plain-local-files
    # path by default, so explicitly force the feature off here.
    monkeypatch.setenv("FORGE_CLIENT_WAREHOUSE_URL", "")
    monkeypatch.setenv("FORGE_CLIENT_WAREHOUSE_PUBLIC_HOST", "")
    monkeypatch.setenv("FORGE_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("FORGE_JWT_SECRET", "test-secret-do-not-use-in-prod-" + "0" * 16)

    from forge_api import db as db_module
    from forge_api import registry as registry_module

    db_module.configure(f"sqlite+aiosqlite:///{db_path}")
    registry_module._RUNS.clear()
    yield runs_dir
    registry_module._RUNS.clear()
    from forge_api import hosted_mcp as hosted_mcp_module

    hosted_mcp_module._sessions.clear()


@asynccontextmanager
async def _lifespan_client(isolated_env, *, authenticated: bool):
    from forge_api import db as db_module
    from forge_api.main import create_app

    isolated_env.mkdir(parents=True, exist_ok=True)
    await db_module.init_db()

    app = create_app()
    if authenticated:
        # Almost every existing test predates login and exercises /runs,
        # /packs directly - overriding the dependency here keeps them
        # unauthenticated-account-free instead of logging in via a real
        # /auth/login call in every test. Auth itself is tested against
        # `unauthenticated_client`, which has no override.
        from forge_api.models_orm import UserORM
        from forge_api.routers.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: UserORM(
            email="test@example.com", password_hash=""
        )

    # pytest-asyncio tears down this fixture on a different task than it
    # entered; MCP's anyio task group refuses that. Enter lifespan for the
    # request, ignore the cross-task cancel-scope error on the way out.
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        with suppress(RuntimeError):
            await lifespan.__aexit__(None, None, None)


@pytest.fixture
async def client(isolated_env):
    async with _lifespan_client(isolated_env, authenticated=True) as ac:
        yield ac


@pytest.fixture
async def unauthenticated_client(isolated_env):
    """Like `client`, but without the auth dependency override - for the
    login flow itself and for asserting protected routes 401 without a
    session cookie."""
    async with _lifespan_client(isolated_env, authenticated=False) as ac:
        yield ac
