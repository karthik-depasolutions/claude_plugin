"""Row-limit injection and query timeout enforcement — the last two guardrails
before a query actually touches DuckDB.
"""

from __future__ import annotations

import threading
from typing import Any

import duckdb
import pandas as pd
from sqlglot import exp

DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_SECONDS = 10

# The runtime shares one DuckDB connection across all tool calls (cached in
# server.create_server's state dict, or per-run in the API's hosted_mcp
# _sessions). `con.interrupt()` is connection-wide, not query-wide, so without
# this lock a slow request's timeout timer can cancel a *different* request's
# query - only reachable over Streamable HTTP, where calls genuinely overlap.
# ponytail: one global query lock; per-request connections if throughput matters.
_QUERY_LOCK = threading.Lock()


class QueryTimeoutError(RuntimeError):
    pass


def enforce_row_limit(statement: exp.Expression, max_rows: int) -> exp.Expression:
    target = statement.this if isinstance(statement, exp.With) else statement
    if not isinstance(target, exp.Select):
        return statement

    existing = target.args.get("limit")
    if existing is not None:
        try:
            current = int(existing.expression.this)
        except (AttributeError, ValueError, TypeError):
            current = max_rows + 1
        if current <= max_rows:
            return statement

    target.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return statement


def run_with_timeout(
    con: duckdb.DuckDBPyConnection, sql: str, timeout_seconds: int, params: dict[str, Any] | None = None
) -> pd.DataFrame:
    with _QUERY_LOCK:
        timer = threading.Timer(timeout_seconds, con.interrupt)
        timer.start()
        try:
            result = con.execute(sql, params or {}).fetchdf()
        except duckdb.InterruptException as exc:
            raise QueryTimeoutError(f"Query exceeded {timeout_seconds}s and was interrupted.") from exc
        finally:
            timer.cancel()
    return result
