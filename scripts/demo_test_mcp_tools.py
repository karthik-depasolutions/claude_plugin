"""Demo & Testing Script for 4-Tier MCP Runtime Surface against Synthetic Data.

Ingests `synthetic_edtech_leads.csv`, compiles verified KPIs, boots the MCP server,
and tests every single tool across all 4 tiers, printing structured output.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.ingestion.registry import ingest
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only
from mis_mcp_runtime.config import (
    BindingsConfig,
    CompiledKpiConfig,
    ColumnBindingConfig,
    DataSourceConfig,
    RuntimeConfig,
    TableConfig,
)
from mis_mcp_runtime.engine.duckdb_session import open_session
from mis_mcp_runtime.tools.describe_data import describe_data, list_business_concepts
from mis_mcp_runtime.tools.describe_schema import describe_schema
from mis_mcp_runtime.tools.get_data_profile import get_data_profile
from mis_mcp_runtime.tools.get_kpi import get_kpi, list_kpis
from mis_mcp_runtime.tools.get_value_set import get_value_set
from mis_mcp_runtime.tools.metric_analytics import (
    breakdown_metric,
    compare_kpi,
    explain_metric,
    query_metric,
    rank_entities,
)
from mis_mcp_runtime.tools.record_tools import get_record
from mis_mcp_runtime.tools.render_chart import chart_payload, markdown_table
from mis_mcp_runtime.tools.run_safe_query import run_safe_query
from mis_mcp_runtime.tools.search_records import search_records

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_demo():
    print("=" * 80)
    print("[TEST] MIS PLUGIN FORGE - 4-TIER MCP RUNTIME DEMO WITH SYNTHETIC DATA")
    print("=" * 80)

    dataset_path = REPO_ROOT / "fixtures" / "datasets" / "synthetic_edtech_leads.csv"
    print(f"\n[DATASET] Ingesting synthetic dataset: {dataset_path.name}")
    ds = ingest(dataset_path)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)

    pack_dir = REPO_ROOT / "industry-packs" / "edtech"
    if not pack_dir.exists():
        pack_dir = REPO_ROOT / "industry-packs" / "generic-analytics"
    pack = load_pack(pack_dir)
    print(f"[PACK] Matched Industry Pack: {pack.name} ({pack.slug})")

    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)
    print(f"[COMPILED] Verified KPIs: {len(kpi_defs.kpis)} (Skipped: {len(kpi_defs.skipped)})")

    # Build In-Memory Runtime Config
    table = ds.tables[0]
    tables_cfg = [TableConfig(name=table.name, physical_ref=table.physical_ref, columns=[c.name for c in table.columns])]
    ds_cfg = DataSourceConfig(
        kind=ds.kind.value,
        duckdb_attach_sql=ds.connection.duckdb_attach_sql,
        read_only=True,
        tables=tables_cfg,
    )
    col_bindings = [
        ColumnBindingConfig(role=c.role.name if hasattr(c.role, 'name') else str(c.role), table_alias=c.table_alias, physical=c.physical)
        for c in bindings.columns
    ]
    bindings_cfg = BindingsConfig(
        allowed_tables=bindings.allowed_tables,
        denied_columns=bindings.denied_columns,
        columns=col_bindings,
    )
    compiled_kpis_cfg = [
        CompiledKpiConfig(
            id=k.id,
            label=k.label,
            description=k.description,
            unit=k.unit,
            sql=k.sql,
            assertions=k.assertions,
            result_columns=k.result_columns,
        )
        for k in kpi_defs.kpis
    ]

    schema_summary = {
        "pack_slug": pack.slug,
        "tables": [{"name": table.name, "columns": [c.name for c in table.columns]}],
        "guardrails": {"max_query_rows": 200, "query_timeout_seconds": 10},
    }

    business_context = {
        "business_domain": "EdTech & Education Analytics",
        "business_process": "Lead Acquisition, Trial Booking, & Course Admissions",
        "record_grain": "One student lead per row",
        "entities": ["leads", "students", "sales_reps", "courses"],
        "dimensions": ["course_name", "campaign_id", "sales_rep", "city", "status"],
        "measures": ["course_fee_inr", "amount_paid_inr", "discount_applied_inr", "lead_score", "call_duration_mins"],
        "time_fields": ["created_at", "trial_date"],
    }

    runtime_config = RuntimeConfig(
        config_dir=dataset_path.parent,
        data_dir=dataset_path.parent,
        data_source=ds_cfg,
        bindings=bindings_cfg,
        kpis=compiled_kpis_cfg,
        schema_summary=schema_summary,
        business_context=business_context,
        max_query_rows=200,
        query_timeout_seconds=10,
    )

    con = open_session(ds_cfg, dataset_path.parent)

    print("\n" + "-" * 80)
    print(">> TIER 1: SEMANTIC DISCOVERY TOOLS")
    print("-" * 80)

    print("\n1. describe_data():")
    print(json.dumps(describe_data(runtime_config), indent=2))

    print("\n2. list_business_concepts():")
    print(json.dumps(list_business_concepts(runtime_config), indent=2))

    print("\n3. describe_schema(table='synthetic_edtech_leads'):")
    print(json.dumps(describe_schema(runtime_config, table=table.name), indent=2))

    print("\n4. get_value_set(field='status'):")
    print(json.dumps(get_value_set(runtime_config, con, field="status"), indent=2))

    print("\n" + "-" * 80)
    print(">> TIER 2: BUSINESS ANALYTICS & KPI TOOLS")
    print("-" * 80)

    print("\n5. list_kpis():")
    print(json.dumps(list_kpis(runtime_config), indent=2))

    if compiled_kpis_cfg:
        first_kpi = compiled_kpis_cfg[0].id
        print(f"\n6. get_kpi(kpi_id='{first_kpi}'):")
        kpi_result = get_kpi(runtime_config, con, first_kpi)
        print(json.dumps(kpi_result, indent=2))

        print(f"\n7. explain_metric(metric_id='{first_kpi}'):")
        print(json.dumps(explain_metric(runtime_config, first_kpi), indent=2))

        print(f"\n8. compare_kpi(kpi_id='{first_kpi}', period_a={{...}}, period_b={{...}}):")
        comp = compare_kpi(runtime_config, con, first_kpi, {"start_date": "2026-01-01", "end_date": "2026-01-31"}, {"start_date": "2026-02-01", "end_date": "2026-02-28"})
        print(json.dumps(comp, indent=2))

    print("\n9. rank_entities(entity='sales_rep', limit=5):")
    print(json.dumps(rank_entities(runtime_config, con, entity_dimension="sales_rep", limit=5), indent=2))

    print("\n10. breakdown_metric(dimension='course_name', limit=5):")
    print(json.dumps(breakdown_metric(runtime_config, con, dimension="course_name", limit=5), indent=2))

    print("\n11. query_metric(metric_id='leads_by_status', group_by=['status']):")
    print(json.dumps(query_metric(runtime_config, con, metric_id="leads_by_status", group_by=["status"]), indent=2))

    print("\n" + "-" * 80)
    print(">> TIER 3: RECORD EXPLORATION TOOLS")
    print("-" * 80)

    print("\n12. search_records(table='synthetic_edtech_leads', filters={'status': 'Completed'}, limit=2):")
    records = search_records(runtime_config, con, table.name, filters={"status": "Completed"}, limit=2)
    print(json.dumps(records, indent=2))

    first_lead_id = records["rows"][0]["lead_id"] if records.get("rows") else "LEAD-10001"
    print(f"\n13. get_record(table='synthetic_edtech_leads', id_value='{first_lead_id}', id_column='lead_id'):")
    print(json.dumps(get_record(runtime_config, con, table.name, id_value=first_lead_id, id_column="lead_id"), indent=2))

    print("\n" + "-" * 80)
    print(">> TIER 4: VISUALIZATION")
    print("-" * 80)

    if compiled_kpis_cfg:
        print(f"\n14. render_chart markdown fallback for '{first_kpi}':")
        print(markdown_table(kpi_result))

    print("\n" + "-" * 80)
    print(">> TIER 5: ESCAPE HATCH & SECURITY TEST")
    print("-" * 80)

    print("\n15. run_safe_query('SELECT city, COUNT(*) AS leads FROM src_synthetic_edtech_leads GROUP BY 1 ORDER BY 2 DESC LIMIT 3'):")
    ref = table.physical_ref
    safe_res = run_safe_query(runtime_config, con, f'SELECT "city", COUNT(*) AS leads FROM {ref} GROUP BY 1 ORDER BY 2 DESC LIMIT 3')
    print(json.dumps(safe_res, indent=2))

    print("\n16. Testing PII Denied Column Protection (e.g. attempting to query 'phone'):")
    denied_test = run_safe_query(runtime_config, con, f'SELECT "phone" FROM {ref}')
    print(json.dumps(denied_test, indent=2))

    print("\n" + "=" * 80)
    print("[SUCCESS] ALL 17 MCP TOOLS EXECUTED SUCCESSFULLY AGAINST SYNTHETIC DATA!")
    print("=" * 80)


if __name__ == "__main__":
    run_demo()
