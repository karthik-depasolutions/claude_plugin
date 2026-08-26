"""Unit tests for safe investigation tools and read-only DuckDB query execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_core.agentic.investigation_tools import (
    build_investigation_tools,
)
from forge_core.ingestion.registry import ingest
from forge_core.profiling import build_structural_only

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS_ROOT = REPO_ROOT / "fixtures" / "datasets"


@pytest.fixture
def bookings_toolkit():
    ds = ingest(DATASETS_ROOT / "bookings.csv")
    structural = build_structural_only(ds)
    tools = build_investigation_tools(
        data_source=ds,
        structural=structural,
        denied_columns={"phone", "customer_name"},
        tenant_id="tenant_123",
        datasource_ref="ds_bookings",
    )
    return {t.name: t for t in tools}


def test_inspect_schema_tool(bookings_toolkit):
    tool = bookings_toolkit["inspect_schema"]
    res = json.loads(tool.invoke({}))
    assert "tables" in res
    assert len(res["tables"]) == 1
    assert res["tables"][0]["name"] == "bookings"
    assert "amount_inr" in res["tables"][0]["columns"]


def test_inspect_column_tool(bookings_toolkit):
    tool = bookings_toolkit["inspect_column"]
    res = json.loads(tool.invoke({"table": "bookings", "column": "amount_inr"}))
    assert res["column"] == "amount_inr"
    assert res["null_pct"] == 0.0
    assert len(res["sample_values"]) > 0


def test_inspect_denied_column_rejected(bookings_toolkit):
    tool = bookings_toolkit["inspect_column"]
    res = tool.invoke({"table": "bookings", "column": "phone"})
    assert "Tool Error" in res
    assert "denied by security/PII guardrails" in res


def test_aggregate_tool(bookings_toolkit):
    tool = bookings_toolkit["aggregate"]
    res = json.loads(tool.invoke({"table": "bookings", "column": "amount_inr", "op": "sum"}))
    assert res["op"] == "sum"
    assert res["result"] is not None
    assert res["result"] > 0


def test_sample_rows_tool(bookings_toolkit):
    tool = bookings_toolkit["sample_rows"]
    res = json.loads(tool.invoke({"table": "bookings", "columns": ["booking_id", "city"], "limit": 5}))
    assert len(res["rows"]) <= 5
    assert "booking_id" in res["rows"][0]


def test_readonly_query_success(bookings_toolkit):
    tool = bookings_toolkit["run_readonly_duckdb_query"]
    res = json.loads(tool.invoke({"query": "SELECT city, COUNT(*) as cnt FROM bookings GROUP BY city", "limit": 10}))
    assert res["row_count"] > 0
    assert "city" in res["columns"]


def test_readonly_query_rejects_modifications(bookings_toolkit):
    tool = bookings_toolkit["run_readonly_duckdb_query"]
    res = tool.invoke({"query": "DROP TABLE bookings"})
    assert "Tool Error" in res


def test_readonly_query_rejects_denied_columns(bookings_toolkit):
    tool = bookings_toolkit["run_readonly_duckdb_query"]
    res = tool.invoke({"query": "SELECT phone FROM bookings"})
    assert "Tool Error" in res
    assert "denied by PII guardrails" in res


def test_tools_record_successful_calls_into_the_evidence_sink(retail_orders_dir):
    """`validation/gates.py::verify_column_claim` checks a claim against this
    log (V1 — "the evidence actually exists"), so a claim the agent makes
    from a tool call it ran this session is only verifiable if the call was
    recorded.

    This parameter was passed by two callers *before it existed*
    (`data_understanding_agent.py`, `understanding/agent.py`). Both wrap
    agent construction in a bare `except Exception`, so the TypeError was
    swallowed and both agents silently returned nothing on every single run
    while still logging an invocation with zero steps and zero tokens. A
    signature this load-bearing needs a test that actually calls it."""
    from forge_core.agentic.investigation_tools import build_investigation_tools
    from forge_core.ingestion.registry import ingest
    from forge_core.profiling import build_structural_only

    data_source = ingest(retail_orders_dir)
    structural = build_structural_only(data_source)
    sink: list[str] = []

    tools = build_investigation_tools(data_source, structural, set(), evidence_sink=sink)
    by_name = {t.name: t for t in tools}

    by_name["inspect_column"].invoke({"table": "orders", "column": "order_status"})
    assert len(sink) == 1
    assert "order_status" in sink[0]

    # A refused call proves nothing and must not become citable evidence -
    # otherwise a claim could cite its own failure as support.
    by_name["inspect_column"].invoke({"table": "orders", "column": "no_such_column"})
    assert len(sink) == 1


def test_evidence_sink_is_optional(retail_orders_dir):
    """Callers that don't need the log must keep working unchanged."""
    from forge_core.agentic.investigation_tools import build_investigation_tools
    from forge_core.ingestion.registry import ingest
    from forge_core.profiling import build_structural_only

    data_source = ingest(retail_orders_dir)
    structural = build_structural_only(data_source)
    tools = build_investigation_tools(data_source, structural, set())
    result = {t.name: t for t in tools}["inspect_schema"].invoke({})
    assert "orders" in result
