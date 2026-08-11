from __future__ import annotations

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

    from forge_api import db as db_module
    from forge_api import registry as registry_module

    db_module.configure(f"sqlite+aiosqlite:///{db_path}")
    registry_module._RUNS.clear()
    yield runs_dir
    registry_module._RUNS.clear()


@pytest.fixture
async def client(isolated_env):
    from forge_api import db as db_module
    from forge_api.main import create_app

    isolated_env.mkdir(parents=True, exist_ok=True)
    await db_module.init_db()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
