"""Exercises check_mcp_smoke against a real config/*.json directory, built
the same way the (future) M9 packager will build one. This mirrors
packages/mcp-runtime/tests/conftest.py's fixture on purpose - once M9 lands
a real `packaging.plugin_builder.write_config`, both call sites should use
it instead of hand-assembling this JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.ingestion.registry import ingest
from forge_core.models.common import CheckStatus
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only
from forge_core.validation.mcp_smoke import check_mcp_smoke

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


@pytest.fixture
def bookings_config_dir(bookings_csv: Path, tmp_path: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    table = ds.tables[0]
    (config_dir / "data_source.json").write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )
    (config_dir / "schema_bindings.json").write_text(bindings.model_dump_json(), encoding="utf-8")
    (config_dir / "kpi_defs.json").write_text(kpi_defs.model_dump_json(), encoding="utf-8")
    return config_dir, bookings_csv.parent, kpi_defs


def test_mcp_smoke_skips_without_config_dir():
    result = check_mcp_smoke(None, None, None)
    assert result.status == CheckStatus.SKIPPED


def test_mcp_smoke_passes_against_real_generated_config(bookings_config_dir):
    config_dir, data_dir, kpi_defs = bookings_config_dir
    result = check_mcp_smoke(config_dir, data_dir, kpi_defs)
    assert result.status == CheckStatus.PASS, result.issues
