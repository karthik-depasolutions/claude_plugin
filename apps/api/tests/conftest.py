from __future__ import annotations

from contextlib import suppress
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

    # The understanding phase is mandatory - keep the API suite hermetic by
    # swapping the real Gemini provider for the deterministic in-process fake.
    from forge_core.testing import FakeLLMProvider

    from forge_api import pipeline_runner as _pr

    monkeypatch.setattr(_pr, "get_provider", lambda role="generation": FakeLLMProvider())
    # A developer's local .env may configure the client warehouse (it's
    # loaded by `Settings(env_file=".env")` regardless of monkeypatch) - the
    # API test suite must stay hermetic and exercise the plain-local-files
    # path by default, so explicitly force the feature off here.
    monkeypatch.setenv("FORGE_CLIENT_WAREHOUSE_URL", "")
    monkeypatch.setenv("FORGE_CLIENT_WAREHOUSE_PUBLIC_HOST", "")
    monkeypatch.setenv("FORGE_PUBLIC_BASE_URL", "")

    from forge_api import db as db_module
    from forge_api import registry as registry_module

    db_module.configure(f"sqlite+aiosqlite:///{db_path}")
    registry_module._RUNS.clear()
    yield runs_dir
    registry_module._RUNS.clear()
    from forge_api import hosted_mcp as hosted_mcp_module

    hosted_mcp_module._sessions.clear()


@pytest.fixture
async def client(isolated_env):
    from forge_api import db as db_module
    from forge_api.main import create_app

    isolated_env.mkdir(parents=True, exist_ok=True)
    await db_module.init_db()

    app = create_app()
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
