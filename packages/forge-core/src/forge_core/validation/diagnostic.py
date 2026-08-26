"""Self-diagnostic and auto-repair reasoning agent for the validation harness.

When validation checks detect errors in AI-suggested metrics, parameters, or bindings:
1. Performs root-cause analysis on the exact check failure and DuckDB error traces.
2. Identifies if the error is isolated to an optional/agent-proposed component or implausible binding.
3. Automatically repairs or safely prunes the failing suggested definition to preserve
   plugin build integrity without lowering guardrails.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_core.compiler.kpi_compiler import compile_all
from forge_core.models.bindings import SchemaBindings
from forge_core.models.common import CheckStatus
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import CompiledKpi, KpiDefsFile
from forge_core.models.metrics import MetricDefinition
from forge_core.models.validation import ValidationCheckResult, ValidationIssue, ValidationReport

logger = logging.getLogger("forge_core.validation.diagnostic")


class DiagnosticRemedy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_name: str
    target_id: str
    action: str = Field(description="'prune_metric' | 'prune_kpi' | 'prune_binding' | 'log_caveat' | 'unrepairable'")
    reason: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remedies: list[DiagnosticRemedy] = Field(default_factory=list)
    repaired: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


def diagnose_and_repair_validation(
    report: ValidationReport,
    kpi_defs: KpiDefsFile,
    metric_defs: list[MetricDefinition] | None = None,
    bindings: SchemaBindings | None = None,
    pack: IndustryPack | None = None,
) -> tuple[ValidationReport, DiagnosticReport]:
    """Inspects validation check results and applies safe deterministic repairs
    to AI-suggested metrics or optional KPIs that failed dry-run or SQL safety,
    or prunes implausible bindings flagged during validation.
    """
    remedies: list[DiagnosticRemedy] = []
    modified_kpis = False
    modified_metrics = False
    modified_bindings = False

    for check_res in report.checks:
        if check_res.status not in (CheckStatus.FAIL, CheckStatus.WARN):
            continue

        for issue in check_res.issues:
            location = issue.location or ""

            # If binding_plausibility flagged an implausible binding, prune it safely
            if check_res.check == "binding_plausibility" and location.startswith("binding:"):
                role = location.split(":", 1)[1]
                if bindings is not None and pack is not None:
                    initial_cols = len(bindings.columns)
                    bindings.columns = [c for c in bindings.columns if c.role != role]
                    if len(bindings.columns) < initial_cols:
                        new_kpis = compile_all(pack, bindings)
                        kpi_defs.kpis = new_kpis.kpis
                        kpi_defs.skipped = new_kpis.skipped
                        remedies.append(
                            DiagnosticRemedy(
                                check_name=check_res.check,
                                target_id=role,
                                action="prune_binding",
                                reason=f"Implausible binding for {role!r} pruned: {issue.message}",
                            )
                        )
                        modified_bindings = True
                        modified_kpis = True

            # If an agent-proposed KPI failed dry-run or validation, prune it from kpi_defs
            for kpi in list(kpi_defs.kpis):
                if kpi.source == "agent_proposed" and (kpi.id in location or location == kpi.id):
                    kpi_defs.kpis.remove(kpi)
                    kpi_defs.skipped[kpi.id] = f"Removed by ValidationDiagnosticAgent: {issue.message}"
                    remedies.append(
                        DiagnosticRemedy(
                            check_name=check_res.check,
                            target_id=kpi.id,
                            action="prune_kpi",
                            reason=f"Agent-suggested KPI {kpi.id} failed validation check {check_res.check}: {issue.message}",
                        )
                    )
                    modified_kpis = True

            # If an agent-proposed metric failed, prune it
            if metric_defs:
                for m in list(metric_defs):
                    if m.source == "agent_proposed" and (m.id in location or location == m.id):
                        metric_defs.remove(m)
                        remedies.append(
                            DiagnosticRemedy(
                                check_name=check_res.check,
                                target_id=m.id,
                                action="prune_metric",
                                reason=f"Agent-suggested metric {m.id} failed validation check {check_res.check}: {issue.message}",
                            )
                        )
                        modified_metrics = True

    repaired = modified_kpis or modified_metrics or modified_bindings
    diag = DiagnosticReport(remedies=remedies, repaired=repaired)
    return report, diag


__all__ = ["DiagnosticRemedy", "DiagnosticReport", "diagnose_and_repair_validation"]
