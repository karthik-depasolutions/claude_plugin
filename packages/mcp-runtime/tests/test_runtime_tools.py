from __future__ import annotations

from pathlib import Path

from mis_mcp_runtime.config import load_runtime_config
from mis_mcp_runtime.engine.duckdb_session import open_session
from mis_mcp_runtime.tools.describe_schema import describe_schema
from mis_mcp_runtime.tools.get_data_profile import get_data_profile
from mis_mcp_runtime.tools.get_kpi import get_kpi, list_kpis
from mis_mcp_runtime.tools.run_safe_query import run_safe_query
from mis_mcp_runtime.tools.search_records import search_records


def _load(bookings_config_dir: Path):
    config = load_runtime_config()
    con = open_session(config.data_source, config.data_dir)
    return config, con


def test_config_fails_closed_on_missing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MIS_MCP_CONFIG_DIR", str(tmp_path / "nope"))
    monkeypatch.setenv("MIS_MCP_DATA_DIR", str(tmp_path))
    import pytest
    from mis_mcp_runtime.config import ConfigError

    with pytest.raises(ConfigError):
        load_runtime_config()


def test_describe_schema_excludes_row_data(bookings_config_dir: Path):
    config, _ = _load(bookings_config_dir)
    result = describe_schema(config)
    assert "tables" in result
    assert "denied_columns" in result
    assert "phone" in result["denied_columns"]
    # Back-compat: a schema_summary.json written before data_context existed
    # must yield {} (a missing key raises) - hosted_mcp runs the current
    # runtime against already-on-disk plugin configs.
    assert result["data_context"] == {}


def test_describe_schema_surfaces_data_context(bookings_config_dir: Path):
    import json

    summary_path = bookings_config_dir / "schema_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["data_context"] = {
        "notes": ["80% of bookings have status 'confirmed' - treated as committed revenue."],
        "findings": [{"code": "dominant_value", "column": "status", "detail": "80% of rows share one value"}],
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    config, _ = _load(bookings_config_dir)
    result = describe_schema(config)
    assert result["data_context"]["notes"] == summary["data_context"]["notes"]
    assert result["data_context"]["findings"][0]["code"] == "dominant_value"


def test_get_data_profile_excludes_denied_columns(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = get_data_profile(config, con, "bookings")
    columns = {c["column"] for c in result["columns"]}
    assert "phone" not in columns
    assert "customer_name" not in columns


def test_list_and_get_kpi(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    listed = list_kpis(config)
    assert len(listed["kpis"]) >= 4

    result = get_kpi(config, con, "total_revenue")
    assert result["row_count"] == 1
    assert result["rows"][0]["total_revenue"] > 0
    assert all(a["passed"] for a in result["assertions"])


def test_get_kpi_unknown_id(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = get_kpi(config, con, "does_not_exist")
    assert "error" in result


def test_run_safe_query_allows_explicit_select(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    fact_ref = config.data_source.tables[0].physical_ref
    result = run_safe_query(config, con, f'SELECT "status", COUNT(*) AS n FROM {fact_ref} GROUP BY "status"')
    assert "rows" in result
    assert result["row_count"] >= 1


def test_run_safe_query_rejects_select_star(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    fact_ref = config.data_source.tables[0].physical_ref
    result = run_safe_query(config, con, f"SELECT * FROM {fact_ref}")
    assert "error" in result


def test_run_safe_query_rejects_denied_column(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    fact_ref = config.data_source.tables[0].physical_ref
    result = run_safe_query(config, con, f'SELECT "phone" FROM {fact_ref}')
    assert "error" in result


def test_run_safe_query_rejects_disallowed_table(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = run_safe_query(config, con, 'SELECT "x" FROM some_other_table')
    assert "error" in result


def test_run_safe_query_rejects_mutations(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    fact_ref = config.data_source.tables[0].physical_ref
    result = run_safe_query(config, con, f'DELETE FROM {fact_ref}')
    assert "error" in result


def test_run_safe_query_injects_row_limit(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    fact_ref = config.data_source.tables[0].physical_ref
    result = run_safe_query(config, con, f'SELECT "booking_id" FROM {fact_ref}')
    assert "LIMIT" in result["executed_sql"].upper()


def test_search_records_excludes_denied_and_filters(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = search_records(config, con, "bookings", filters={"status": "Completed"}, limit=5)
    assert result["row_count"] <= 5
    for row in result["rows"]:
        assert "phone" not in row
        assert "customer_name" not in row


def test_search_records_rejects_denied_filter_column(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = search_records(config, con, "bookings", filters={"phone": "123"})
    assert "error" in result


def test_normalize_env_value_unwraps_pasted_export_lines():
    from mis_mcp_runtime.engine.duckdb_session import _normalize_env_value

    url = "postgresql://u:p@host:5432/db?sslmode=require"
    assert _normalize_env_value(url) == url
    assert _normalize_env_value(f'FORGE_SOURCE_DB_URL="{url}"') == url
    assert _normalize_env_value(f'export FORGE_SOURCE_DB_URL="{url}"') == url
    assert _normalize_env_value(f'"{url}"') == url
