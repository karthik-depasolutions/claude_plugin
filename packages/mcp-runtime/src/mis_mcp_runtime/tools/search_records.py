"""`search_records` — constrained equality-filter lookup against one allowed
table. Column and table names are validated against the allow/deny lists
before being interpolated into SQL (they can never come from untrusted
values); filter *values* are always passed as bound parameters.
"""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.engine.rows import to_json_rows
from mis_mcp_runtime.security.limits import QueryTimeoutError, run_with_timeout


def search_records(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    table: str,
    filters: dict[str, Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    table_cfg = next((t for t in config.data_source.tables if t.name == table), None)
    if table_cfg is None or table_cfg.physical_ref not in config.bindings.allowed_tables:
        return {
            "error": f"Table {table!r} is not queryable. "
            f"Allowed tables: {[t.name for t in config.data_source.tables]}"
        }

    denied = set(config.bindings.denied_columns)
    available_columns = [c for c in table_cfg.columns if c not in denied]
    filters = filters or {}

    unknown = [k for k in filters if k not in available_columns]
    if unknown:
        return {"error": f"Unknown or denied filter column(s): {unknown}"}

    select_cols = ", ".join(f'"{c}"' for c in available_columns)
    params: dict[str, Any] = {}
    where_parts = []
    for i, (key, value) in enumerate(filters.items()):
        param_name = f"p{i}"
        where_parts.append(f'"{key}" = ${param_name}')
        params[param_name] = value
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    capped_limit = min(limit, config.max_query_rows)
    sql = f"SELECT {select_cols} FROM {table_cfg.physical_ref} {where_sql} LIMIT {capped_limit}"

    try:
        df = run_with_timeout(con, sql, config.query_timeout_seconds, params=params)
    except QueryTimeoutError as exc:
        return {"error": str(exc)}

    rows = to_json_rows(df)
    return {"rows": rows, "row_count": len(rows)}
