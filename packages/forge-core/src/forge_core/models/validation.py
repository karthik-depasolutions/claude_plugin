"""Validation harness contracts — Stage 5 output. See
forge_core.validation.harness for the check implementations and
docs/architecture.md §5 for why each of the eight checks exists.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.common import CheckStatus

CHECK_NAMES = (
    "fact_check",
    "sql_safety",
    "dry_run",
    "pii_scan",
    "plugin_spec",
    "cli_validate",
    "mcp_smoke",
    "self_critique",
)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    location: str
    message: str
    suggestion: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str
    status: CheckStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    skipped_reason: str | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: str
    generated_at: str
    checks: list[ValidationCheckResult]
    overall: CheckStatus

    def check(self, name: str) -> ValidationCheckResult | None:
        for c in self.checks:
            if c.check == name:
                return c
        return None

    @property
    def hard_failures(self) -> list[ValidationCheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @staticmethod
    def compute_overall(checks: list[ValidationCheckResult]) -> CheckStatus:
        if any(c.status == CheckStatus.FAIL for c in checks):
            return CheckStatus.FAIL
        if any(c.status == CheckStatus.WARN for c in checks):
            return CheckStatus.WARN
        return CheckStatus.PASS
