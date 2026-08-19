"""Check 8 - hooks smoke test.

The SessionStart guardrail injection is a `command` handler running
`hooks/session_context.py` (see generation/hooks.py) - a `prompt` handler on
SessionStart would silently never fire. This check actually executes that
script against the packaged plugin and asserts it exits 0 with non-empty
stdout, so a broken hook command is caught here rather than at the customer's
first session start.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from forge_core.models.common import CheckStatus
from forge_core.models.validation import ValidationCheckResult, ValidationIssue


def check_hooks_smoke(plugin_dir: Path | None) -> ValidationCheckResult:
    if plugin_dir is None:
        return ValidationCheckResult(
            check="hooks_smoke",
            status=CheckStatus.SKIPPED,
            skipped_reason="no packaged plugin directory was provided (run after the packaging stage)",
        )

    script = plugin_dir / "hooks" / "session_context.py"
    if not script.is_file():
        return ValidationCheckResult(
            check="hooks_smoke",
            status=CheckStatus.FAIL,
            issues=[
                ValidationIssue(
                    severity="error",
                    location="hooks/session_context.py",
                    message="SessionStart command handler references hooks/session_context.py, which is missing",
                )
            ],
        )

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ValidationCheckResult(
            check="hooks_smoke",
            status=CheckStatus.FAIL,
            issues=[
                ValidationIssue(
                    severity="error",
                    location="hooks/session_context.py",
                    message=f"hook script could not be executed: {exc}",
                )
            ],
        )

    if proc.returncode != 0:
        return ValidationCheckResult(
            check="hooks_smoke",
            status=CheckStatus.FAIL,
            issues=[
                ValidationIssue(
                    severity="error",
                    location="hooks/session_context.py",
                    message=f"hook script exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}",
                )
            ],
        )

    if not proc.stdout.strip():
        return ValidationCheckResult(
            check="hooks_smoke",
            status=CheckStatus.FAIL,
            issues=[
                ValidationIssue(
                    severity="error",
                    location="hooks/session_context.py",
                    message="hook script printed nothing to stdout - SessionStart context would be empty",
                )
            ],
        )

    return ValidationCheckResult(check="hooks_smoke", status=CheckStatus.PASS, issues=[])