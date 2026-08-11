#!/usr/bin/env python3
"""MCP server — auto-packaged from validated KPI specs."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
from mcp.server.fastmcp import FastMCP

from kpi_logic import compute_kpi

TABLE_NAME = "bookings"
MAX_ROWS = 200
SUPPORTED_KPIS = ['total_revenue', 'repeat_customer_rate', 'cancellation_rate', 'monthly_revenue_trend', 'bookings_by_city', 'number_of_repeat_customers', 'revenue_by_lab_partner_and_package']
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|COPY|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)

mcp = FastMCP("curelo-bookings")
_df: pd.DataFrame | None = None
_con: duckdb.DuckDBPyConnection | None = None


def _csv_path() -> Path:
    env = os.environ.get("BOOKINGS_CSV")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "sample_bookings_mis.csv"


def _load() -> tuple[pd.DataFrame, duckdb.DuckDBPyConnection]:
    global _df, _con
    if _df is None:
        path = _csv_path()
        if not path.exists():
            raise FileNotFoundError(f"Bookings CSV not found: {path}")
        _df = pd.read_csv(path)
        _con = duckdb.connect(":memory:")
        _con.register(TABLE_NAME, _df)
    return _df, _con  # type: ignore[return-value]


def _validate_select(sql: str) -> None:
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("Query is empty.")
    if FORBIDDEN.search(stripped):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if not re.match(r"^SELECT\b", stripped, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed.")
    if ";" in stripped:
        raise ValueError("Multiple statements are not allowed.")


@mcp.tool()
def describe_schema() -> str:
    """Return column names, types, row count, and three sample rows."""
    df, _ = _load()
    sample = df.head(3).where(pd.notnull(df), None).to_dict(orient="records")
    payload = {
        "table": TABLE_NAME,
        "row_count": len(df),
        "columns": [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns],
        "sample_rows": sample,
    }
    return json.dumps(payload, indent=2, default=str)


@mcp.tool()
def run_safe_query(sql: str) -> str:
    """Execute a read-only SELECT against bookings (max 200 rows)."""
    _, con = _load()
    try:
        _validate_select(sql)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, indent=2)
    try:
        result = con.execute(sql).fetchdf()
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)
    truncated = len(result) > MAX_ROWS
    if truncated:
        result = result.head(MAX_ROWS)
    rows = result.where(pd.notnull(result), None).to_dict(orient="records")
    return json.dumps(
        {"row_count": len(rows), "truncated": truncated, "max_rows": MAX_ROWS, "rows": rows},
        indent=2,
        default=str,
    )


@mcp.tool()
def get_kpi(kpi_name: str) -> str:
    """Return a named KPI computed from the bookings dataset."""
    df, _ = _load()
    try:
        return json.dumps(compute_kpi(df, kpi_name), indent=2, default=str)
    except ValueError:
        return json.dumps(
            {"error": f"Unsupported kpi_name: {kpi_name!r}", "supported_kpis": SUPPORTED_KPIS},
            indent=2,
        )


if __name__ == "__main__":
    try:
        _load()
    except Exception as exc:
        print(f"Failed to load bookings data: {exc}", file=sys.stderr)
        sys.exit(1)
    mcp.run()
