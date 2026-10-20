"""Opens a DuckDB session from a generated plugin's `data_source.json` by
replaying its `duckdb_attach_sql`, substituting `{DATA_DIR}` for the actual
data directory and `${ENV_VAR}` for a live-database credential resolved
from this process's environment at connect time (see
forge_core.ingestion.postgres — a connection string never gets written to
config/data_source.json, only the name of the env var to read it from).
This mirrors forge_core.runtime_session by design — same contract,
independent implementation, because this package ships standalone.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Mapping
from pathlib import Path

import duckdb
import pandas as pd

from mis_mcp_runtime.config import ConfigError, DataSourceConfig

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")
_ATTACH_RETRY_ATTEMPTS = 5
_ATTACH_RETRY_BASE_DELAY_S = 3.0


def _normalize_env_value(value: str) -> str:
    """Windows users often paste `export VAR="postgresql://..."` or
    `VAR="postgresql://..."` as the env var *value*. DuckDB then treats the
    prefix as a connection option and fails. Take the URL from the first
    postgres scheme, if any, and strip wrapping quotes."""
    stripped = value.strip().strip('"').strip("'")
    for marker in ("postgresql://", "postgres://"):
        idx = stripped.lower().find(marker)
        if idx != -1:
            return stripped[idx:].strip().strip('"').strip("'")
    return stripped


def _resolve_env_vars(stmt: str, lookup: Mapping[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = lookup.get(var_name)
        if not value:
            raise ConfigError(
                f"data_source.json references ${{{var_name}}}, but that environment variable "
                "isn't set - this plugin needs a live database credential to run."
            )
        return _normalize_env_value(value)

    return _ENV_VAR_PATTERN.sub(_replace, stmt)


def open_session(
    data_source: DataSourceConfig, data_dir: Path, *, env: Mapping[str, str] | None = None
) -> duckdb.DuckDBPyConnection:
    """Replay duckdb_attach_sql. ATTACH to a pooled Postgres (Supabase
    Supavisor) can fail transiently right after connect, so a failed ATTACH
    is retried on a fresh DuckDB connection rather than reused.

    `env`, when given, is consulted for `${VAR}` credentials before
    `os.environ` - the hosted (Pattern B) gateway passes this run's own
    source credential, which never sits in the API process's environment.
    Stdio plugins pass nothing and resolve straight from `os.environ`."""
    import time

    lookup: Mapping[str, str] = {**os.environ, **env} if env else os.environ
    resolved_dir = data_dir.resolve()
    last_error: Exception | None = None
    for attempt in range(_ATTACH_RETRY_ATTEMPTS):
        con = duckdb.connect(":memory:")
        try:
            for stmt in data_source.duckdb_attach_sql:
                if stmt.startswith("-- excel:"):
                    _, view_name, path_template = stmt.split(":", 2)
                    excel_path = Path(path_template.replace("{DATA_DIR}", resolved_dir.as_posix()))
                    df = pd.read_excel(excel_path)
                    con.register(view_name, df)
                    continue
                resolved_stmt = _resolve_env_vars(
                    stmt.replace("{DATA_DIR}", resolved_dir.as_posix()), lookup
                )
                con.execute(resolved_stmt)
            con.execute("SET enable_progress_bar = false")
            # Make DuckDB's Postgres scanner tolerant of real-world schemas
            # (jsonb columns the catalog reports as numeric, mixed arrays, ...).
            # No-op when this session has no live-Postgres attach. Kept in sync
            # with forge_core.ingestion.postgres.apply_pg_compat.
            for pg_stmt in ("SET pg_array_as_varchar = true", "SET pg_use_binary_copy = false"):
                with contextlib.suppress(duckdb.Error):
                    con.execute(pg_stmt)
            return con
        except duckdb.Error as exc:
            last_error = exc
            con.close()
            if attempt < _ATTACH_RETRY_ATTEMPTS - 1:
                time.sleep(_ATTACH_RETRY_BASE_DELAY_S * (attempt + 1))
    assert last_error is not None
    raise last_error
