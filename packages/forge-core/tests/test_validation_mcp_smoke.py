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


def test_mcp_smoke_hands_the_subprocess_the_runs_credential_not_the_process_env(tmp_path, monkeypatch):
    """The spawned runtime must connect to the same database every in-process
    stage used - the run's credential overlay - not a stale FORGE_SOURCE_DB_URL
    left in the API server's own environment. Regression for run 6c51a51a1c8c."""
    from forge_core.ingestion.credentials import set_credentials
    from forge_core.validation.mcp_smoke import _plugin_connection_credentials

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "data_source.json").write_text(
        json.dumps(
            {"connection": {"duckdb_attach_sql": [
                "INSTALL postgres; LOAD postgres;",
                "ATTACH '${FORGE_SOURCE_DB_URL}' AS srcdb (TYPE POSTGRES, READ_ONLY)",
            ]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FORGE_SOURCE_DB_URL", "postgresql://stale@server-env/db")

    with set_credentials({"FORGE_SOURCE_DB_URL": "postgresql://real@run-overlay/db"}):
        assert _plugin_connection_credentials(cfg) == {
            "FORGE_SOURCE_DB_URL": "postgresql://real@run-overlay/db"
        }
    # no overlay (the CLI) -> process env, unchanged behaviour
    assert _plugin_connection_credentials(cfg) == {"FORGE_SOURCE_DB_URL": "postgresql://stale@server-env/db"}
    # a file-source plugin has no ${VAR} to resolve
    (cfg / "data_source.json").write_text(
        json.dumps({"connection": {"duckdb_attach_sql": ["CREATE VIEW t AS SELECT * FROM '{DATA_DIR}/t.csv'"]}}),
        encoding="utf-8",
    )
    assert _plugin_connection_credentials(cfg) == {}


def test_looks_unreachable_distinguishes_dead_db_from_a_flaky_query():
    from forge_core.validation.mcp_smoke import _looks_unreachable

    assert _looks_unreachable(
        "{'code': 'query_failed', 'message': 'IO Error: Unable to connect to Postgres at ...'}"
    )
    assert _looks_unreachable("FATAL: (ECIRCUITBREAKER) too many authentication failures")
    assert not _looks_unreachable("{'code': 'query_failed', 'message': 'syntax error near \"GROUP\"'}")
    assert not _looks_unreachable(None)


def test_mcp_smoke_passes_against_real_generated_config(bookings_config_dir):
    config_dir, data_dir, kpi_defs = bookings_config_dir
    result = check_mcp_smoke(config_dir, data_dir, kpi_defs)
    assert result.status == CheckStatus.PASS, result.issues
