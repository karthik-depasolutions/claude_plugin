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

import os
import re
from pathlib import Path

import duckdb
import pandas as pd

from mis_mcp_runtime.config import ConfigError, DataSourceConfig

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(stmt: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if not value:
            raise ConfigError(
                f"data_source.json references ${{{var_name}}}, but that environment variable "
                "isn't set - this plugin needs a live database credential to run."
            )
        return value

    return _ENV_VAR_PATTERN.sub(_replace, stmt)


def open_session(data_source: DataSourceConfig, data_dir: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    resolved_dir = data_dir.resolve()

    for stmt in data_source.duckdb_attach_sql:
        if stmt.startswith("-- excel:"):
            _, view_name, path_template = stmt.split(":", 2)
            excel_path = Path(path_template.replace("{DATA_DIR}", resolved_dir.as_posix()))
            df = pd.read_excel(excel_path)
            con.register(view_name, df)
            continue
        resolved_stmt = _resolve_env_vars(stmt.replace("{DATA_DIR}", resolved_dir.as_posix()))
        con.execute(resolved_stmt)

    con.execute("SET enable_progress_bar = false")
    return con
