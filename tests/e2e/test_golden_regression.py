"""Golden regression: the canonical curelo-bookings MIS dataset
(fixtures/datasets/bookings.csv - the same data behind
fixtures/golden/curelo-bookings/, the original single-customer POC output)
must still produce a fully valid plugin end-to-end. The legacy fixture's KPI
ids don't match 1:1 (packs were rewritten to canonical roles), so this
asserts the same *set of business questions* still compiles and validates,
rather than diffing bytes against the old POC output.
"""

from __future__ import annotations

from pathlib import Path

from forge_core.models.common import CheckStatus, RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline

EXPECTED_KPI_IDS = {
    "total_revenue",
    "cancellation_rate",
    "monthly_revenue_trend",
    "repeat_customer_rate",
    "number_of_repeat_customers",
    "bookings_by_location",
    "revenue_by_partner_and_product",
}


def test_bookings_dataset_still_produces_a_fully_valid_plugin(bookings_csv: Path, tmp_path: Path):
    record = RunRecord(
        run_id="golden-curelo-bookings",
        source_path=str(bookings_csv),
        output_dir=str(tmp_path),
        industry_override="healthcare-diagnostics",
    )

    result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT)

    # P1-08: every one of this pack's bindings sits in the 0.45-0.62
    # confidence band (see review P0.2 - "Healthcare example is better but
    # not safe") - none clear MIN_CONFIDENCE_RESOLVED on name evidence
    # alone, so the run correctly pauses to confirm them rather than
    # shipping silently. This is the gate working as designed, not a
    # regression - confirm each proposed binding (the resolver's own top
    # pick, which is in fact correct here) and resume.
    assert result.status == RunStatus.NEEDS_INPUT, result.error
    assert result.binding_questions
    result.binding_confirmations = {q.role: q.physical for q in result.binding_questions}
    result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT)

    assert result.status == RunStatus.SUCCEEDED, result.error

    compile_event = next(e for e in result.events if e.stage.value == "compile_kpis" and "skipped" in e.data)
    assert compile_event.data["skipped"] == {}

    validate_event = next(e for e in reversed(result.events) if e.stage.value == "validate")
    report = validate_event.data["report"]
    assert report["overall"] in (CheckStatus.PASS.value, CheckStatus.WARN.value)

    checks_by_name = {c["check"]: c for c in report["checks"]}
    for hard_check in ("fact_check", "sql_safety", "dry_run", "pii_scan", "plugin_spec", "cli_validate"):
        assert checks_by_name[hard_check]["status"] == CheckStatus.PASS.value, checks_by_name[hard_check]

    package_event = next(e for e in reversed(result.events) if e.stage.value == "package")
    plugin_dir = Path(package_event.data["plugin_dir"])
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin_dir / ".mcp.json").is_file()

    kpi_defs_path = plugin_dir / "config" / "kpi_defs.json"
    assert kpi_defs_path.is_file()
    import json

    kpi_ids = {kpi["id"] for kpi in json.loads(kpi_defs_path.read_text(encoding="utf-8"))["kpis"]}
    assert kpi_ids == EXPECTED_KPI_IDS
