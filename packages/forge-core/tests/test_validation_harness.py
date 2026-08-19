from __future__ import annotations

import os
from pathlib import Path

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.registry import ingest
from forge_core.llm import get_provider
from forge_core.models.common import CheckStatus
from forge_core.models.schema_profile import SchemaProfile
from forge_core.models.validation import CHECK_NAMES
from forge_core.profiling import build_structural_only
from forge_core.validation import run_harness
from forge_core.validation.dry_run import check_dry_run
from forge_core.validation.facts import check_facts
from forge_core.validation.pii import check_pii
from forge_core.validation.self_critique import check_self_critique
from forge_core.validation.sql_safety import check_sql_safety

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


@pytest.fixture(autouse=True)
def _cassette_mode(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", os.environ.get("FORGE_LLM_CASSETTE_MODE", "replay"))
    monkeypatch.setenv("FORGE_LLM_CASSETTE_DIR", "fixtures/cassettes")


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def _pipeline(source_path: Path, pack_slug: str):
    profile = _profile_for(source_path)
    pack = load_pack(PACKS_ROOT / pack_slug)
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)
    generated = generate_plugin_content(pack, kpi_defs, profile.source, provider=None)
    return profile, pack, bindings, kpi_defs, generated


def test_healthy_plugin_passes_every_runnable_check(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")

    report = run_harness(
        pack=pack, profile=profile, bindings=bindings, kpi_defs=kpi_defs, generated=generated
    )

    assert {c.check for c in report.checks} == set(CHECK_NAMES)
    for name in ("plugin_spec", "cli_validate", "mcp_smoke", "self_critique"):
        assert report.check(name).status == CheckStatus.SKIPPED
    for name in ("fact_check", "sql_safety", "dry_run", "pii_scan"):
        result = report.check(name)
        assert result.status == CheckStatus.PASS, f"{name}: {result.issues}"
    # SKIPPED checks (no packaged plugin dir / no LLM provider yet) don't fail the run.
    assert report.overall == CheckStatus.PASS


def test_fact_check_flags_skipped_required_kpi(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, _ = _pipeline(bookings_csv, "healthcare-diagnostics")
    result = check_facts(pack, bindings, profile, skipped_kpi_ids=["total_revenue"])
    assert result.status == CheckStatus.FAIL
    assert any("total_revenue" in i.location for i in result.issues)


def test_sql_safety_rejects_denied_column_projection(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, _ = _pipeline(bookings_csv, "healthcare-diagnostics")
    denied = bindings.denied_columns[0]
    tampered = kpi_defs.model_copy(deep=True)
    tampered.kpis[0].sql = f'SELECT "{denied}" FROM {bindings.table("fact").physical}'

    result = check_sql_safety(tampered, bindings)
    assert result.status == CheckStatus.FAIL
    assert any(denied in i.message for i in result.issues)


def test_sql_safety_rejects_select_star(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, _ = _pipeline(bookings_csv, "healthcare-diagnostics")
    tampered = kpi_defs.model_copy(deep=True)
    tampered.kpis[0].sql = f'SELECT * FROM {bindings.table("fact").physical}'

    result = check_sql_safety(tampered, bindings)
    assert result.status == CheckStatus.FAIL


def test_dry_run_flags_failing_assertion(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, _ = _pipeline(bookings_csv, "healthcare-diagnostics")
    tampered = kpi_defs.model_copy(deep=True)
    tampered.kpis[0].assertions = ["1 == 2"]

    result = check_dry_run(tampered, profile.source)
    assert result.status == CheckStatus.FAIL
    assert any("assertion failed" in i.message for i in result.issues)


def test_pii_scan_flags_denied_column_in_sql(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, _ = _pipeline(bookings_csv, "healthcare-diagnostics")
    denied = bindings.denied_columns[0]
    tampered = kpi_defs.model_copy(deep=True)
    tampered.kpis[0].sql = f'SELECT "{denied}" AS leaked FROM {bindings.table("fact").physical}'

    result = check_pii(tampered, bindings, generated_texts={})
    assert result.status == CheckStatus.FAIL


def test_pii_scan_warns_on_email_like_text_in_artifact(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, _ = _pipeline(bookings_csv, "healthcare-diagnostics")
    result = check_pii(kpi_defs, bindings, generated_texts={"skill": "Contact us at hello@example.com"})
    assert result.status == CheckStatus.WARN


def test_harness_is_generic_across_multi_table_and_sqlite(retail_orders_dir: Path, edtech_sqlite: Path):
    for source_path, pack_slug in ((retail_orders_dir, "retail-ecommerce"), (edtech_sqlite, "edtech")):
        profile, pack, bindings, kpi_defs, generated = _pipeline(source_path, pack_slug)
        report = run_harness(
            pack=pack, profile=profile, bindings=bindings, kpi_defs=kpi_defs, generated=generated
        )
        assert report.check("dry_run").status == CheckStatus.PASS
        assert report.check("sql_safety").status == CheckStatus.PASS


def test_self_critique_runs_with_real_provider_and_stays_grounded(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    provider = get_provider(role="critique")

    report = run_harness(
        pack=pack, profile=profile, bindings=bindings, kpi_defs=kpi_defs, generated=generated,
        provider=provider,
    )

    result = report.check("self_critique")
    assert result.status in (CheckStatus.PASS, CheckStatus.WARN, CheckStatus.FAIL)


class _StubCritiqueProvider:
    def __init__(self, findings: list[dict]) -> None:
        self._findings = findings

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        return {"findings": self._findings}

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


def test_self_critique_drops_false_positive_about_allowed_mcp_tools(bookings_csv: Path):
    _, pack, _, kpi_defs, generated = _pipeline(bookings_csv, "generic-analytics")
    provider = _StubCritiqueProvider(
        [
            {
                "severity": "error",
                "location": "agents/generic-analytics-analyst.md - data retrieval tools",
                "message": (
                    "The agent is configured to use `get_data_profile` and `search_records` tools. "
                    "These tools can return 'metrics' or 'numbers' (e.g., column statistics, "
                    "individual record values) that are not included in the list of ONLY real, "
                    "verified KPIs (`average_measure`, `count_by_category`, `sum_measure`, "
                    "`total_records`, `trend_by_month`), violating the constraint on allowed data points."
                ),
            }
        ]
    )

    result = check_self_critique(pack, kpi_defs, {"agent": generated.agent_body}, provider)

    assert result.status == CheckStatus.PASS
    assert result.issues == []


def test_self_critique_drops_false_positive_about_skipped_kpi_ids(bookings_csv: Path):
    """A dataset with no numeric measure at all skips average_measure/
    sum_measure/trend_by_month (see generic-analytics's `optional: true` on
    those KPIs). SKILL.md correctly lists them under "Not available for
    this data source" - the critic must not treat that as an invented KPI
    id just because the message names no tool and only quotes skipped ids."""
    _, pack, _, kpi_defs, generated = _pipeline(bookings_csv, "generic-analytics")
    tampered_defs = kpi_defs.model_copy(deep=True)
    tampered_defs.skipped = {
        "average_measure": "test", "sum_measure": "test", "trend_by_month": "test"
    }
    provider = _StubCritiqueProvider(
        [
            {
                "severity": "error",
                "location": f"skills/{generated.skill_name}/SKILL.md",
                "message": (
                    "KPI IDs `average_measure`, `sum_measure`, and `trend_by_month` are listed as "
                    "available but are not in the verified KPI catalog."
                ),
            }
        ]
    )

    result = check_self_critique(pack, tampered_defs, {"skill": generated.skill_body}, provider)

    assert result.status == CheckStatus.PASS
    assert result.issues == []


def test_self_critique_still_fails_on_invented_kpi_id(bookings_csv: Path):
    _, pack, _, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    provider = _StubCritiqueProvider(
        [
            {
                "severity": "error",
                "location": "skills/analyst.md",
                "message": "Invented KPI id `made_up_revenue` is not in the catalog.",
            }
        ]
    )

    result = check_self_critique(pack, kpi_defs, {"agent": generated.agent_body}, provider)

    assert result.status == CheckStatus.FAIL
    assert any("made_up_revenue" in i.message for i in result.issues)


def test_mcp_smoke_tool_failed_includes_payload_and_is_error_text():
    from types import SimpleNamespace

    from forge_core.validation.mcp_smoke import _tool_failed
    from mcp.types import TextContent

    ok = SimpleNamespace(is_error=False, content=[TextContent(type="text", text='{"kpi_id": "x", "rows": []}')])
    assert _tool_failed(ok) is None

    payload_err = SimpleNamespace(
        is_error=False, content=[TextContent(type="text", text='{"error": "invalid role OID"}')]
    )
    assert "invalid role OID" in (_tool_failed(payload_err) or "")

    protocol_err = SimpleNamespace(is_error=True, content=[TextContent(type="text", text="connection refused")])
    assert _tool_failed(protocol_err) == "connection refused"


def test_self_critique_keeps_pii_finding_even_when_it_names_an_allowed_tool(bookings_csv: Path):
    _, pack, _, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    provider = _StubCritiqueProvider(
        [
            {
                "severity": "error",
                "location": "agents/analyst.md",
                "message": "The `search_records` tool is instructed to return phone numbers, which is PII.",
            }
        ]
    )

    result = check_self_critique(pack, kpi_defs, {"agent": generated.agent_body}, provider)

    assert result.status == CheckStatus.FAIL
    assert any("PII" in i.message for i in result.issues)
