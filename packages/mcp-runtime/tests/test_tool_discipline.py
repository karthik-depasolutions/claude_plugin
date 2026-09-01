"""The MCP surface's discipline layer: annotations on every tool, and the
`tool_guard` error envelope + redaction. See SECURITY.md.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from mis_mcp_runtime.errors import _GENERIC_MESSAGE, ToolError, classify, redact, tool_guard
from mis_mcp_runtime.security.allowlist import AllowlistError
from mis_mcp_runtime.security.sql_policy import SqlPolicyError

_META_TOOLS = {"describe_schema", "list_business_concepts", "list_kpis", "explain_metric"}
_DATA_TOOLS = {"run_safe_query", "get_kpi", "search_records", "describe_data", "get_data_profile"}


def _tools(bookings_config_dir: Path):
    from mis_mcp_runtime.server import create_server

    return {t.name: t for t in create_server()._tool_manager.list_tools()}


def test_every_tool_declares_read_only_non_destructive_annotations(bookings_config_dir: Path):
    tools = _tools(bookings_config_dir)
    assert tools
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is True, name
        assert tool.annotations.destructive_hint is False, name


def test_open_world_hint_separates_config_reads_from_db_reads(bookings_config_dir: Path):
    tools = _tools(bookings_config_dir)
    for name in _META_TOOLS & tools.keys():
        assert tools[name].annotations.open_world_hint is False, name
    for name in _DATA_TOOLS & tools.keys():
        assert tools[name].annotations.open_world_hint is True, name


def test_redact_strips_dsn_paths_and_internal_table_names():
    dirty = (
        "connect postgresql://u:secret@db.example.com:5432/app failed; "
        "cache at /var/lib/forge/x.db, table client_sparda_leads_71fa35b970ae"
    )
    clean = redact(dirty)
    assert "postgresql://" not in clean
    assert "secret" not in clean
    assert "/var/lib" not in clean
    assert "client_sparda_leads_71fa35b970ae" not in clean


def test_classify_maps_known_errors_and_hides_unexpected_ones():
    import duckdb

    assert classify(SqlPolicyError("only SELECT allowed"))[0] == "denied"
    assert classify(AllowlistError("nope"))[0] == "denied"
    assert classify(KeyError("kpi_x"))[0] == "not_found"
    assert classify(ToolError("invalid_argument", "bad limit")) == ("invalid_argument", "bad limit")

    # A bad-SQL DuckDB error is the caller's fault, not a server crash: keep
    # the (redacted) message so the model can fix its query.
    try:
        duckdb.connect(":memory:").execute("SELECT missing_col FROM (SELECT 1 AS a)")
    except duckdb.Error as exc:
        code, message = classify(exc)
    assert code == "query_failed"
    assert "missing_col" in message

    code, message = classify(RuntimeError("boom postgresql://u:p@h/db"))
    assert code == "internal_error"
    assert message == _GENERIC_MESSAGE
    assert "postgresql" not in message


def test_tool_guard_turns_exceptions_into_a_uniform_envelope():
    @tool_guard
    def raises() -> dict:
        raise AllowlistError("table not allowed: secret_table")

    result = raises()
    assert result == {"error": {"code": "denied", "message": "table not allowed: secret_table"}}


def test_tool_guard_normalizes_and_redacts_a_string_error_return():
    @tool_guard
    def self_reports_error() -> dict:
        return {"error": "query failed on postgresql://u:p@h/db"}

    result = self_reports_error()
    assert result["error"]["code"] == "query_failed"
    assert "postgresql://" not in result["error"]["message"]


def test_tool_guard_preserves_the_wrapped_signature_for_schema_introspection():
    def original(a: int, b: str = "x") -> dict:
        return {"a": a, "b": b}

    assert inspect.signature(tool_guard(original)) == inspect.signature(original)
