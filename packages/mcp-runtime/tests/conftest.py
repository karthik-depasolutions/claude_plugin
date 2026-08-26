"""Builds a real config/*.json fixture by running the actual forge-core
pipeline (ingest -> profile -> bind -> compile) against the bookings.csv
fixture. forge_core is only a *test* dependency here — mis_mcp_runtime's
production code never imports it (see server.py's module docstring).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def bookings_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from forge_core.binding import resolve_bindings
    from forge_core.classification import load_pack
    from forge_core.compiler import compile_all
    from forge_core.ingestion.registry import ingest
    from forge_core.models.schema_profile import SchemaProfile
    from forge_core.profiling import build_structural_only

    monkeypatch.setenv("FORGE_ENABLE_PII_PROTECTION", "true")
    source = REPO_ROOT / "fixtures" / "datasets" / "bookings.csv"
    ds = ingest(source)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)

    pack = load_pack(REPO_ROOT / "industry-packs" / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    table = ds.tables[0]
    data_source_json = {
        "kind": ds.kind.value,
        "connection": {
            "duckdb_attach_sql": ds.connection.duckdb_attach_sql,
            "read_only": ds.connection.read_only,
        },
        "tables": [
            {
                "name": table.name,
                "physical_ref": table.physical_ref,
                "columns": [c.name for c in table.columns],
            }
        ],
    }
    (config_dir / "data_source.json").write_text(json.dumps(data_source_json), encoding="utf-8")
    (config_dir / "schema_bindings.json").write_text(
        bindings.model_dump_json(), encoding="utf-8"
    )
    (config_dir / "kpi_defs.json").write_text(kpi_defs.model_dump_json(), encoding="utf-8")
    (config_dir / "schema_summary.json").write_text(
        json.dumps(
            {
                "pack_slug": pack.slug,
                "denied_columns": sorted(bindings.denied_columns),
                "guardrails": {
                    "max_query_rows": pack.guardrails.max_query_rows,
                    "query_timeout_seconds": pack.guardrails.query_timeout_seconds,
                },
                "tables": [{"name": table.name, "columns": [c.name for c in table.columns]}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MIS_MCP_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("MIS_MCP_DATA_DIR", str(source.parent))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    return config_dir
