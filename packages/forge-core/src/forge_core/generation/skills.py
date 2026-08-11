"""Stage 5a — SKILL.md generation.

The KPI catalog and guardrail notes are rendered deterministically straight
from the compiled artifacts (never hallucinated); only the framing intro
paragraph is optionally LLM-authored. Every fact the file states — KPI ids,
labels, guardrail text — is substituted in from real pipeline output, so a
missing or misbehaving LLM can only make the prose blander, never wrong.
"""

from __future__ import annotations

from forge_core.generation.constants import mcp_tool_ref
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.plugin_spec import SkillFrontmatter

_FALLBACK_INTRO_TEMPLATE = (
    "Use this skill when the user asks questions about {name} data - revenue, volumes, "
    "trends, or any of the KPIs listed below. It explains how to reach for the bundled MCP "
    "tools instead of guessing at column names or writing ad-hoc SQL from scratch."
)


def skill_name(pack: IndustryPack) -> str:
    return f"{pack.slug}-analyst"[:64]


def _kpi_catalog_markdown(kpi_defs: KpiDefsFile) -> str:
    lines = [
        f"- `{kpi.id}` - **{kpi.label}**: {kpi.description} (unit: {kpi.unit})" for kpi in kpi_defs.kpis
    ]
    if kpi_defs.skipped:
        lines.append("")
        lines.append(
            "Not available for this data source (required data was missing): "
            + ", ".join(f"`{k}`" for k in kpi_defs.skipped)
        )
    return "\n".join(lines)


def _generate_intro(pack: IndustryPack, provider: LLMProvider | None) -> str:
    if provider is None:
        return _FALLBACK_INTRO_TEMPLATE.format(name=pack.name)
    prompt = (
        "Write exactly one short paragraph (2-3 sentences, plain prose, no markdown headers) "
        f"that tells an AI assistant when to use a skill for analyzing {pack.name} business data. "
        f"Industry description: {pack.description}\n"
        "Do not invent specific numbers, company names, or KPI values."
    )
    try:
        text = provider.generate_text(prompt).strip()
        return text or _FALLBACK_INTRO_TEMPLATE.format(name=pack.name)
    except LLMError:
        return _FALLBACK_INTRO_TEMPLATE.format(name=pack.name)


def generate_skill(
    pack: IndustryPack, kpi_defs: KpiDefsFile, provider: LLMProvider | None = None
) -> tuple[SkillFrontmatter, str]:
    """Return (frontmatter, body) for `skills/<name>/SKILL.md`."""
    intro = _generate_intro(pack, provider)
    catalog = _kpi_catalog_markdown(kpi_defs)
    guardrail_lines = "\n".join(f"- {note}" for note in pack.guardrails.notes) or (
        "- Never request, display, or infer personally identifiable information."
    )
    body = (
        f"# {pack.name} Analyst\n\n"
        f"{intro}\n\n"
        "## Available KPIs\n\n"
        f"{catalog}\n\n"
        "## How to use the MCP tools\n\n"
        f"1. Call `{mcp_tool_ref('describe_schema')}` or `{mcp_tool_ref('list_kpis')}` first if "
        "you're unsure what's available.\n"
        f"2. Prefer `{mcp_tool_ref('get_kpi')}` with one of the KPI ids above over writing SQL.\n"
        f"3. Use `{mcp_tool_ref('run_safe_query')}` only for questions no KPI answers, and always "
        "as a read-only SELECT with explicit columns.\n\n"
        "## Guardrails\n\n"
        f"{guardrail_lines}\n"
    )
    top_kpi_ids = ", ".join(k.id for k in kpi_defs.kpis[:5])
    description = (
        f"Use this skill when analyzing {pack.name} data - revenue, volumes, trends, and "
        f"pre-built KPIs ({top_kpi_ids}). Guides querying via the bundled MCP tools instead "
        "of ad-hoc SQL."
    )[:1024]
    frontmatter = SkillFrontmatter(
        name=skill_name(pack),
        description=description,
        metadata={"pack_slug": pack.slug, "pack_version": pack.version},
    )
    return frontmatter, body
