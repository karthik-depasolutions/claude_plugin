"""`create_all` only creates missing *tables*, never alters an existing one,
so a dev/test SQLite database made before a column existed keeps the old
shape and every query touching the new column fails at runtime.

`init_db` closes that gap by diffing the live table against the ORM metadata.
This used to be a hand-maintained list of ALTER statements, which drifted
(0005's token columns were missing from it) - these tests pin the derived
behaviour so adding a column to a model is genuinely all that's needed.
"""

from __future__ import annotations

import sqlite3

import pytest

# A 0004-era `runs` table: everything up to use_agent, none of the token
# columns 0005 added. Written as raw DDL on purpose - the point is to
# reproduce a database created by an older version of the code.
LEGACY_SCHEMA = """
CREATE TABLE runs (
    run_id VARCHAR(64) PRIMARY KEY,
    status VARCHAR(32),
    current_stage VARCHAR(32),
    source_path TEXT,
    output_dir TEXT,
    industry_override VARCHAR(64),
    error TEXT,
    record_json JSON,
    binding_overrides_json JSON,
    created_at DATETIME,
    updated_at DATETIME,
    tenant_id VARCHAR(255),
    use_llm BOOLEAN,
    use_agent BOOLEAN
);
CREATE TABLE users (
    email VARCHAR(255) PRIMARY KEY,
    password_hash VARCHAR(255),
    created_at DATETIME
);
INSERT INTO runs (run_id, status) VALUES ('pre-existing-run', 'succeeded');
INSERT INTO users (email, password_hash) VALUES ('old@example.com', 'x');
"""


def _columns(db_path, table: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    con.executescript(LEGACY_SCHEMA)
    con.commit()
    con.close()

    from forge_api import db as db_module

    monkeypatch.setenv("FORGE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    db_module.configure(f"sqlite+aiosqlite:///{db_path}")
    return db_path


async def test_init_db_adds_columns_the_orm_gained_since_the_db_was_made(legacy_db):
    from forge_api import db as db_module

    assert "total_tokens" not in _columns(legacy_db, "runs")

    await db_module.init_db()

    runs = _columns(legacy_db, "runs")
    assert {"input_tokens", "output_tokens", "total_tokens", "llm_calls"} <= runs
    # Not just the columns this test was written for: whatever the ORM
    # declares must be present, so a future column is covered too.
    from forge_api.models_orm import RunORM, UserORM

    assert {c.name for c in RunORM.__table__.columns} <= runs
    assert {c.name for c in UserORM.__table__.columns} <= _columns(legacy_db, "users")


async def test_existing_rows_are_backfilled_not_dropped(legacy_db):
    from forge_api import db as db_module

    await db_module.init_db()

    con = sqlite3.connect(legacy_db)
    try:
        row = con.execute(
            "SELECT run_id, total_tokens, llm_calls FROM runs WHERE run_id = 'pre-existing-run'"
        ).fetchone()
        admin = con.execute(
            "SELECT is_admin FROM users WHERE email = 'old@example.com'"
        ).fetchone()
    finally:
        con.close()

    # The run predates token accounting; 0 is the honest value for it, and
    # the row itself must survive the migration untouched.
    assert row == ("pre-existing-run", 0, 0)
    assert admin[0] == 0


async def test_init_db_is_idempotent(legacy_db):
    """It runs on every API startup, so a second pass must not try to re-add
    a column it already added."""
    from forge_api import db as db_module

    await db_module.init_db()
    await db_module.init_db()

    assert "total_tokens" in _columns(legacy_db, "runs")
