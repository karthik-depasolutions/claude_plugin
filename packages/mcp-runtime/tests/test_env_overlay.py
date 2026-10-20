from __future__ import annotations

import pytest
from mis_mcp_runtime.config import ConfigError
from mis_mcp_runtime.engine.duckdb_session import _resolve_env_vars


def test_resolve_env_vars_prefers_the_given_lookup_over_process_env(monkeypatch):
    monkeypatch.setenv("FORGE_SOURCE_DB_URL", "postgresql://stale@process-env/db")
    stmt = "ATTACH '${FORGE_SOURCE_DB_URL}' AS srcdb (TYPE POSTGRES, READ_ONLY)"

    # hosted gateway path: an explicit per-run overlay wins
    overlaid = _resolve_env_vars(stmt, {"FORGE_SOURCE_DB_URL": "postgresql://real@run-overlay/db"})
    assert "real@run-overlay" in overlaid and "stale" not in overlaid

    # stdio path: plain os.environ still works
    import os

    assert "stale@process-env" in _resolve_env_vars(stmt, os.environ)


def test_resolve_env_vars_still_errors_when_the_var_is_nowhere():
    with pytest.raises(ConfigError):
        _resolve_env_vars("ATTACH '${MISSING_VAR}' AS x", {})
