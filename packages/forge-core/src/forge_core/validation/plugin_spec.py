"""Check 5 - plugin spec.

Structural validation of an already-packaged plugin directory against the
real `claude` CLI constraints verified in docs/plugin-format.md (see plan
§4): BOM-less UTF-8 everywhere, `plugin.json` shape, component paths that
actually exist (honoring whatever the manifest declares, not just the
conventional directory names), and every component's frontmatter restricted
to its portable field set. This is what would otherwise only be caught by
shelling out to `claude plugin validate` (check 6) - doing it in pure
Python means the check still runs in environments without the CLI
installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pydantic

from forge_core.models.common import CheckStatus
from forge_core.models.plugin_spec import (
    AgentFrontmatter,
    CommandFrontmatter,
    HooksFile,
    McpConfig,
    PluginManifest,
    SkillFrontmatter,
)
from forge_core.models.validation import ValidationCheckResult, ValidationIssue
from forge_core.validation.frontmatter import FrontmatterError, parse_frontmatter

_TEXT_SUFFIXES = {".json", ".md"}


def _check_no_bom(plugin_dir: Path) -> list[ValidationIssue]:
    issues = []
    for path in plugin_dir.rglob("*"):
        if path.is_file() and path.suffix in _TEXT_SUFFIXES:
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        location=str(path.relative_to(plugin_dir)),
                        message="file has a UTF-8 BOM; Claude Code rejects this with "
                        "'Invalid JSON syntax: Unrecognized token'",
                    )
                )
    return issues


def load_manifest(plugin_dir: Path) -> tuple[PluginManifest | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_path = manifest_dir / "plugin.json"

    if not manifest_path.is_file():
        issues.append(
            ValidationIssue(
                severity="error", location=".claude-plugin/plugin.json", message="manifest is missing"
            )
        )
        return None, issues

    extra_files = [p.name for p in manifest_dir.iterdir() if p.name != "plugin.json"]
    if extra_files:
        issues.append(
            ValidationIssue(
                severity="error",
                location=".claude-plugin/",
                message=f"only plugin.json may live here, found: {extra_files}",
            )
        )

    try:
        manifest = PluginManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, pydantic.ValidationError) as exc:
        issues.append(
            ValidationIssue(severity="error", location=str(manifest_path), message=f"invalid manifest: {exc}")
        )
        return None, issues

    for field_name in ("skills", "commands", "agents", "hooks", "mcpServers"):
        value = getattr(manifest, field_name)
        if value is None:
            continue
        for rel_path in [value] if isinstance(value, str) else value:
            if not (plugin_dir / rel_path).exists():
                issues.append(
                    ValidationIssue(
                        severity="error",
                        location=f"plugin.json:{field_name}",
                        message=f"declared path {rel_path!r} does not exist",
                    )
                )

    return manifest, issues


def _declared_paths(value: str | list[str] | None, default: str) -> list[str]:
    if value is None:
        return [default]
    return [value] if isinstance(value, str) else value


def _resolve_skill_dirs(plugin_dir: Path, declared: str | list[str] | None) -> list[Path]:
    dirs: list[Path] = []
    for rel in _declared_paths(declared, "./skills/"):
        target = plugin_dir / rel
        if (target / "SKILL.md").is_file():
            dirs.append(target)
        elif target.is_dir():
            dirs.extend(sorted(p for p in target.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()))
    return dirs


def _resolve_md_files(plugin_dir: Path, declared: str | list[str] | None, default: str) -> list[Path]:
    files: list[Path] = []
    for rel in _declared_paths(declared, default):
        target = plugin_dir / rel
        if target.is_file() and target.suffix == ".md":
            files.append(target)
        elif target.is_dir():
            files.extend(sorted(target.glob("*.md")))
    return files


def _check_skills(plugin_dir: Path, declared: str | list[str] | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for skill_dir in _resolve_skill_dirs(plugin_dir, declared):
        skill_md = skill_dir / "SKILL.md"
        location = str(skill_md.relative_to(plugin_dir))
        try:
            data, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            frontmatter = SkillFrontmatter.model_validate(data)
        except (FrontmatterError, pydantic.ValidationError) as exc:
            issues.append(ValidationIssue(severity="error", location=location, message=str(exc)))
            continue
        if frontmatter.name != skill_dir.name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=location,
                    message=f"frontmatter name {frontmatter.name!r} must equal "
                    f"directory name {skill_dir.name!r}",
                )
            )
    return issues


def _check_md_components(
    plugin_dir: Path, declared: str | list[str] | None, default: str, model: type[pydantic.BaseModel]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for md_file in _resolve_md_files(plugin_dir, declared, default):
        location = str(md_file.relative_to(plugin_dir))
        try:
            data, _ = parse_frontmatter(md_file.read_text(encoding="utf-8"))
            model.model_validate(data)
        except (FrontmatterError, pydantic.ValidationError) as exc:
            issues.append(ValidationIssue(severity="error", location=location, message=str(exc)))
    return issues


def _check_hooks_and_mcp(plugin_dir: Path, manifest: PluginManifest | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    hooks_rel = manifest.hooks if manifest else None
    hooks_path = plugin_dir / (hooks_rel or "./hooks/hooks.json")
    if hooks_path.is_file():
        try:
            HooksFile.model_validate(json.loads(hooks_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, pydantic.ValidationError) as exc:
            issues.append(
                ValidationIssue(
                    severity="error", location=str(hooks_path.relative_to(plugin_dir)), message=str(exc)
                )
            )

    mcp_rel = manifest.mcpServers if manifest else None
    mcp_path = plugin_dir / (mcp_rel or "./.mcp.json")
    if mcp_path.is_file():
        try:
            McpConfig.model_validate(json.loads(mcp_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, pydantic.ValidationError) as exc:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=str(mcp_path.relative_to(plugin_dir)),
                    message=f"invalid MCP config (a URL-based server missing an explicit 'type' looks "
                    f"exactly like this): {exc}",
                )
            )
    return issues


def check_plugin_spec(plugin_dir: Path | None) -> ValidationCheckResult:
    if plugin_dir is None:
        return ValidationCheckResult(
            check="plugin_spec",
            status=CheckStatus.SKIPPED,
            skipped_reason="no packaged plugin directory was provided (run after the packaging stage)",
        )

    issues = _check_no_bom(plugin_dir)
    manifest, manifest_issues = load_manifest(plugin_dir)
    issues += manifest_issues
    issues += _check_skills(plugin_dir, manifest.skills if manifest else None)
    issues += _check_md_components(
        plugin_dir, manifest.agents if manifest else None, "./agents/", AgentFrontmatter
    )
    issues += _check_md_components(
        plugin_dir, manifest.commands if manifest else None, "./commands/", CommandFrontmatter
    )
    issues += _check_hooks_and_mcp(plugin_dir, manifest)

    status = CheckStatus.FAIL if any(i.severity == "error" for i in issues) else CheckStatus.PASS
    return ValidationCheckResult(check="plugin_spec", status=status, issues=issues)
