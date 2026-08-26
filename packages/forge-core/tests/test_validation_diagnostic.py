"""Unit tests for the ValidationDiagnosticAgent and self-repair loop."""

from __future__ import annotations

from forge_core.models.common import CheckStatus
from forge_core.models.kpi import CompiledKpi, KpiDefsFile
from forge_core.models.metrics import AggOp, MetricDefinition
from forge_core.models.validation import ValidationCheckResult, ValidationIssue, ValidationReport
from forge_core.validation.diagnostic import diagnose_and_repair_validation


def test_diagnose_and_repair_prunes_failing_agent_kpi():
    """Validates that a broken agent-proposed KPI is safely removed without affecting pack KPIs."""
    pack_kpi = CompiledKpi(
        id="pack_kpi_1",
        label="Pack KPI",
        description="Pack KPI desc",
        unit="count",
        sql="SELECT count(*) FROM fact",
        source_kpi_id="pack_kpi_1",
        source="pack",
    )
    bad_agent_kpi = CompiledKpi(
        id="bad_agent_kpi",
        label="Bad Agent KPI",
        description="Bad query",
        unit="inr",
        sql="SELECT nonexistent_col FROM fact",
        source_kpi_id="bad_agent_kpi",
        source="agent_proposed",
    )
    kpi_defs = KpiDefsFile(
        pack_slug="healthcare-diagnostics",
        generated_at="2026-08-25T12:00:00Z",
        kpis=[pack_kpi, bad_agent_kpi],
    )

    failing_check = ValidationCheckResult(
        check="dry_run",
        status=CheckStatus.FAIL,
        issues=[
            ValidationIssue(
                severity="error",
                location="bad_agent_kpi",
                message="Column 'nonexistent_col' does not exist",
            )
        ],
    )
    report = ValidationReport(
        plugin_name="test-plugin",
        generated_at="2026-08-25T12:00:00Z",
        checks=[failing_check],
        overall=CheckStatus.FAIL,
    )

    _, diag = diagnose_and_repair_validation(report, kpi_defs)

    assert diag.repaired is True
    assert len(diag.remedies) == 1
    assert diag.remedies[0].action == "prune_kpi"
    assert diag.remedies[0].target_id == "bad_agent_kpi"

    # Verify bad KPI was pruned and pack KPI remains
    assert len(kpi_defs.kpis) == 1
    assert kpi_defs.kpis[0].id == "pack_kpi_1"
    assert "bad_agent_kpi" in kpi_defs.skipped


def test_diagnose_and_repair_prunes_failing_agent_metric():
    """Validates that a failing agent-suggested metric definition is safely pruned."""
    good_metric = MetricDefinition(
        id="total_bookings",
        label="Total Bookings",
        description="Count of bookings",
        base_entity="bookings",
        measure_column="booking_id",
        aggregation=AggOp.COUNT,
        unit="count",
        source="generated",
    )
    bad_metric = MetricDefinition(
        id="bad_agent_metric",
        label="Bad Metric",
        description="Broken metric",
        base_entity="bookings",
        measure_column="broken_col",
        aggregation=AggOp.SUM,
        unit="inr",
        source="agent_proposed",
    )
    kpi_defs = KpiDefsFile(
        pack_slug="healthcare-diagnostics",
        generated_at="2026-08-25T12:00:00Z",
        kpis=[],
    )
    metric_defs = [good_metric, bad_metric]

    failing_check = ValidationCheckResult(
        check="sql_safety",
        status=CheckStatus.FAIL,
        issues=[
            ValidationIssue(
                severity="error",
                location="bad_agent_metric",
                message="Invalid measure column",
            )
        ],
    )
    report = ValidationReport(
        plugin_name="test-plugin",
        generated_at="2026-08-25T12:00:00Z",
        checks=[failing_check],
        overall=CheckStatus.FAIL,
    )

    _, diag = diagnose_and_repair_validation(report, kpi_defs, metric_defs)

    assert diag.repaired is True
    assert len(diag.remedies) == 1
    assert diag.remedies[0].action == "prune_metric"
    assert len(metric_defs) == 1
    assert metric_defs[0].id == "total_bookings"
