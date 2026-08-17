"""Re-opens a DuckDB session against a `DataSource` from its
`ConnectionContract`. This is the one function that turns the *description*
of a data source back into something queryable — used by the profiler
(immediately after ingest), the binding dry-run, and the validation harness's
dry-run check. The generic MCP runtime has its own copy of this logic
(`mis_mcp_runtime.engine.duckdb_session`) because it must not depend on
forge_core at all — see docs/architecture.md §4.6.

For a live-database source (see forge_core.ingestion.postgres),
`duckdb_attach_sql` references a credential as `${ENV_VAR}` rather than
embedding it — `PostgresAdapter.ingest` sets that env var for the lifetime
of the generator process, so every later re-open in the same pipeline run
(profiling, dry-run, dashboard generation) resolves it the exact same way
the shipped runtime will on the customer's machine.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb
import pandas as pd

from forge_core.models.datasource import DataSource

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def default_data_dir(data_source: DataSource) -> Path:
    if not data_source.connection.original_paths:
        raise ValueError(f"DataSource {data_source.id!r} has no original_paths to infer a directory from.")
    return Path(data_source.connection.original_paths[0]).resolve().parent


def _resolve_env_vars(stmt: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if not value:
            raise ValueError(
                f"duckdb_attach_sql references ${{{var_name}}}, but that environment variable isn't set."
            )
        stripped = value.strip().strip('"').strip("'")
        for marker in ("postgresql://", "postgres://"):
            idx = stripped.lower().find(marker)
            if idx != -1:
                return stripped[idx:].strip().strip('"').strip("'")
        return stripped

    return _ENV_VAR_PATTERN.sub(_replace, stmt)


def open_session(data_source: DataSource, data_dir: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    resolved_dir = None
    if data_source.connection.original_paths:
        resolved_dir = (data_dir or default_data_dir(data_source)).resolve()

    for stmt in data_source.connection.duckdb_attach_sql:
        if stmt.startswith("-- excel:"):
            assert resolved_dir is not None
            _, view_name, path_template = stmt.split(":", 2)
            excel_path = Path(path_template.replace("{DATA_DIR}", resolved_dir.as_posix()))
            df = pd.read_excel(excel_path)
            con.register(view_name, df)
            continue
        resolved_stmt = stmt if resolved_dir is None else stmt.replace("{DATA_DIR}", resolved_dir.as_posix())
        con.execute(_resolve_env_vars(resolved_stmt))

    return con
