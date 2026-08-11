"""Check 6 - shell out to the real `claude` CLI.

Skipped with a warning when the CLI binary isn't on PATH (true for most
dev/CI sandboxes); enforced for real - and gating - whenever it is
available, per plan §5/§6.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from forge_core.models.common import CheckStatus
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

CLI_BINARY = "claude"
_DEFAULT_TIMEOUT_SECONDS = 60


def check_cli_validate(
    plugin_dir: Path | None, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
) -> ValidationCheckResult:
    if plugin_dir is None:
        return ValidationCheckResult(
            check="cli_validate",
            status=CheckStatus.SKIPPED,
            skipped_reason="no packaged plugin directory was provided",
        )

    binary = shutil.which(CLI_BINARY)
    if binary is None:
        return ValidationCheckResult(
            check="cli_validate",
            status=CheckStatus.WARN,
            skipped_reason=f"'{CLI_BINARY}' CLI not found on PATH; this check must be enforced in CI",
        )

    try:
        result = subprocess.run(
            [binary, "plugin", "validate", str(plugin_dir), "--strict"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ValidationCheckResult(
            check="cli_validate",
            status=CheckStatus.FAIL,
            issues=[ValidationIssue(severity="error", location=str(plugin_dir), message=f"timed out: {exc}")],
        )

    if result.returncode == 0:
        return ValidationCheckResult(
            check="cli_validate", status=CheckStatus.PASS, details={"stdout": result.stdout}
        )

    return ValidationCheckResult(
        check="cli_validate",
        status=CheckStatus.FAIL,
        issues=[
            ValidationIssue(
                severity="error",
                location=str(plugin_dir),
                message=f"claude plugin validate --strict failed (exit {result.returncode})",
                details={"stdout": result.stdout, "stderr": result.stderr},
            )
        ],
    )
