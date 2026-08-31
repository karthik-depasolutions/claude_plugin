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
    assert "customer_name" in result["denied_columns"]


def test_get_data_profile_excludes_denied_columns(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = get_data_profile(config, con, "bookings")
    columns = {c["column"] for c in result["columns"]}
    assert "phone" in columns  # a normal column, no longer denied
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
    result = run_safe_query(config, con, f'SELECT "customer_name" FROM {fact_ref}')
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
        assert "customer_name" not in row


def test_search_records_rejects_denied_filter_column(bookings_config_dir: Path):
    config, con = _load(bookings_config_dir)
    result = search_records(config, con, "bookings", filters={"customer_name": "x"})
    assert "error" in result


def test_normalize_env_value_unwraps_pasted_export_lines():
    from mis_mcp_runtime.engine.duckdb_session import _normalize_env_value

    url = "postgresql://u:p@host:5432/db?sslmode=require"
    assert _normalize_env_value(url) == url
    assert _normalize_env_value(f'FORGE_SOURCE_DB_URL="{url}"') == url
    assert _normalize_env_value(f'export FORGE_SOURCE_DB_URL="{url}"') == url
    assert _normalize_env_value(f'"{url}"') == url


def test_describe_data_and_business_concepts(bookings_config_dir: Path):
    from mis_mcp_runtime.tools.describe_data import describe_data, list_business_concepts

    config, _ = _load(bookings_config_dir)
    data_desc = describe_data(config)
    assert "business_domain" in data_desc
    assert "entities" in data_desc
    assert data_desc["available_kpis"] >= 4

    concepts = list_business_concepts(config)
    assert "entities" in concepts
    assert "dimensions" in concepts


def test_schema_drift_report(bookings_config_dir: Path):
    from dataclasses import replace

    from mis_mcp_runtime.config import TableConfig
    from mis_mcp_runtime.schema_drift import schema_drift_report

    config, con = _load(bookings_config_dir)
    assert schema_drift_report(config, con) is None  # config matches the live data

    # A config that claims a column the live table doesn't have -> drift.
    t = config.data_source.tables[0]
    drifted_tables = [replace(t, columns=[*t.columns, "ghost_column"])]
    drifted = replace(config.data_source, tables=drifted_tables)
    report = schema_drift_report(replace(config, data_source=drifted), con)
    assert report is not None
    assert "ghost_column" in report and "SCHEMA DRIFT" in report


def test_describe_schema_targeted(bookings_config_dir: Path):
    config, _ = _load(bookings_config_dir)
    result = describe_schema(config, table="bookings")
    assert len(result["tables"]) == 1
    assert result["tables"][0]["name"] == "bookings"

    invalid_res = describe_schema(config, table="non_existent")
    assert "error" in invalid_res


def test_get_value_set_success_and_denied_rejection(bookings_config_dir: Path):
    from mis_mcp_runtime.tools.get_value_set import get_value_set

    config, con = _load(bookings_config_dir)
    res = get_value_set(config, con, field="status")
    assert "values" in res
    assert len(res["values"]) > 0
    assert any(v["value"] == "Completed" for v in res["values"])

    # Denied column must be rejected
    denied_res = get_value_set(config, con, field="customer_name")
    assert "error" in denied_res
    assert "denied" in denied_res["error"].lower()


def test_metric_analytics_tools(bookings_config_dir: Path):
    from mis_mcp_runtime.tools.metric_analytics import (
        breakdown_metric,
        compare_kpi,
        explain_metric,
        query_metric,
        rank_entities,
    )

    config, con = _load(bookings_config_dir)

    # 1. explain_metric
    explained = explain_metric(config, "total_revenue")
    assert explained["is_verified"] is True
    assert "sql_expression" in explained

    # 2. compare_kpi
    compared = compare_kpi(config, con, "total_revenue")
    assert "absolute_change" in compared
    assert "period_a" in compared

    # 3. breakdown_metric
    breakdown = breakdown_metric(config, con, dimension="status")
    assert len(breakdown["slices"]) > 0
    assert "share_percent" in breakdown["slices"][0]

    # Denied breakdown must be rejected
    assert "error" in breakdown_metric(config, con, dimension="customer_name")

    # 4. rank_entities
    ranked = rank_entities(config, con, entity_dimension="package_name", limit=5)
    assert len(ranked["ranked_results"]) <= 5
    assert ranked["ranked_results"][0]["rank"] == 1

    # Denied entity ranking must be rejected
    assert "error" in rank_entities(config, con, entity_dimension="customer_name")

    # 5. query_metric
    qm = query_metric(config, con, metric_id="bookings_count", group_by=["status"])
    assert "rows" in qm
    assert len(qm["rows"]) > 0


def test_record_exploration_tools(bookings_config_dir: Path):
    from mis_mcp_runtime.tools.record_tools import get_record

    config, con = _load(bookings_config_dir)
    first_row = con.execute('SELECT "booking_id" FROM src_bookings LIMIT 1').fetchone()[0]

    rec = get_record(config, con, table_or_entity="bookings", id_value=first_row, id_column="booking_id")
    assert rec["found"] is True
    assert "customer_name" not in rec["record"]

    # Denied ID lookup
    denied_rec = get_record(config, con, table_or_entity="bookings", id_value="123", id_column="customer_name")
    assert "error" in denied_rec


async def test_schema_resources_are_registered_and_readable(bookings_config_dir: Path):
    import json as _json

    from mis_mcp_runtime.server import create_server

    mcp = create_server()
    uris = {str(r.uri).rstrip("/") for r in await mcp.list_resources()}
    assert {
        "schema://overview",
        "schema://model",
        "schema://cookbook",
        "schema://relationships",
        "schema://patterns",
        "schema://statistics",
    } <= uris

    contents = list(await mcp.read_resource("schema://model"))
    text = "".join(
        c.content if isinstance(c.content, str) else c.content.decode("utf-8") for c in contents
    )
    _json.loads(text)  # schema://model must be valid JSON
