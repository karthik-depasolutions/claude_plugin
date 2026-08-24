"""Stage 6 - the validation harness. Runs all ten checks described in
plan §5 and gates packaging on `fail`.

Checks 6-9 (`plugin_spec`, `cli_validate`, `mcp_smoke`, `hooks_smoke`) need an
already-packaged plugin directory / config directory - before M9 exists (or
when called mid-pipeline, pre-packaging) they report `skipped` rather than
being silently omitted, so a `ValidationReport` always accounts for all
ten checks by name.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from forge_core.generation import GeneratedPlugin
from forge_core.llm.provider import LLMProvider
from forge_core.models.bindings import SchemaBindings
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.schema_profile import SchemaProfile
from forge_core.models.validation import ValidationCheckResult, ValidationReport
from forge_core.validation.cli_validate import check_cli_validate
from forge_core.validation.dry_run import check_dry_run
from forge_core.validation.facts import check_facts
from forge_core.validation.hooks_smoke import check_hooks_smoke
from forge_core.validation.mcp_smoke import check_mcp_smoke
from forge_core.validation.pii import check_pii
from forge_core.validation.plausibility import check_binding_plausibility
from forge_core.validation.plugin_spec import check_plugin_spec
from forge_core.validation.self_critique import check_self_critique
from forge_core.validation.sql_safety import check_sql_safety


def _prose_texts(generated: GeneratedPlugin) -> dict[str, str]:
    """Skill/agent/command prose only - stable, LLM-authored text worth
    reviewing for hallucinated facts. Deliberately excludes the dashboard
    snapshot, which embeds a generation timestamp and real computed numbers
    rather than reviewable prose."""
    texts = {
        f"skills/{generated.skill_name}/SKILL.md": generated.skill_body,
        f"agents/{generated.agent_name}.md": generated.agent_body,
    }
    for command in generated.commands:
        texts[f"commands/{command.name}.md"] = command.body
    return texts


def _pii_scan_texts(generated: GeneratedPlugin) -> dict[str, str]:
    texts = dict(_prose_texts(generated))
    texts["artifacts/dashboard.html"] = generated.dashboard_html
    return texts


def run_harness(
    *,
    pack: IndustryPack,
    profile: SchemaProfile,
    bindings: SchemaBindings,
    kpi_defs: KpiDefsFile,
    generated: GeneratedPlugin,
    provider: LLMProvider | None = None,
    plugin_dir: Path | None = None,
    config_dir: Path | None = None,
    data_dir: Path | None = None,
    plugin_name: str | None = None,
    on_check: Callable[[ValidationCheckResult], None] | None = None,
) -> ValidationReport:
    """`on_check`, if given, fires right after each check finishes - lets a
    caller (the orchestrator) surface per-check progress instead of one
    opaque block, since several of these checks (dry_run, cli_validate,
    mcp_smoke, self_critique) can each individually run for tens of seconds."""

    def _run(check_fn: Callable[..., ValidationCheckResult], *args: object) -> ValidationCheckResult:
        result = check_fn(*args)
        if on_check is not None:
            on_check(result)
        return result

    checks: list[ValidationCheckResult] = [
        _run(check_facts, pack, bindings, profile, kpi_defs.skipped),
        _run(check_sql_safety, kpi_defs, bindings),
        _run(check_binding_plausibility, bindings, profile, pack, provider),
        _run(check_dry_run, kpi_defs, profile.source),
        _run(check_pii, kpi_defs, bindings, _pii_scan_texts(generated)),
        _run(check_plugin_spec, plugin_dir),
        _run(check_cli_validate, plugin_dir),
        _run(check_mcp_smoke, config_dir, data_dir, kpi_defs),
        _run(check_hooks_smoke, plugin_dir),
        _run(check_self_critique, pack, kpi_defs, _prose_texts(generated), provider),
    ]

    return ValidationReport(
        plugin_name=plugin_name or f"{pack.slug}-mis-plugin",
        generated_at=datetime.now(UTC).isoformat(),
        checks=checks,
        overall=ValidationReport.compute_overall(checks),
    )


__all__ = ["run_harness"]
