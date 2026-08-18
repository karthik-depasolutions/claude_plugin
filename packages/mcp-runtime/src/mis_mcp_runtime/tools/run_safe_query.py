"""`run_safe_query` — the one tool that accepts arbitrary SQL text from the
model. Every guardrail module runs, in order, before DuckDB ever sees the
query: parse shape -> table allow-list -> denied columns -> row limit ->
timeout-bounded execution.
"""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.engine.rows import to_json_rows
from mis_mcp_runtime.security.allowlist import AllowlistError, check_tables_allowed
from mis_mcp_runtime.security.limits import QueryTimeoutError, enforce_row_limit, run_with_timeout
from mis_mcp_runtime.security.pii_policy import PiiPolicyError, check_no_denied_columns
from mis_mcp_runtime.security.sql_policy import SqlPolicyError, parse_single_select


def run_safe_query(config: RuntimeConfig, con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    try:
        statement = parse_single_select(sql)
        check_tables_allowed(statement, config.bindings.allowed_tables)
        check_no_denied_columns(statement, config.bindings.denied_columns)
        statement = enforce_row_limit(statement, config.max_query_rows)
        final_sql = statement.sql(dialect="duckdb")
        df = run_with_timeout(con, final_sql, config.query_timeout_seconds)
    except (SqlPolicyError, AllowlistError, PiiPolicyError, QueryTimeoutError) as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}

    rows = to_json_rows(df)
    return {"rows": rows, "row_count": len(rows), "executed_sql": final_sql}
