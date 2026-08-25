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
from forge_core.models.quality import render_data_context

_FALLBACK_INTRO_TEMPLATE = (
    "Use this skill when the user asks questions about {name} data - revenue, volumes, "
    "trends, or any of the KPIs listed below. It explains how to reach for the bundled MCP "
    "tools instead of guessing at column names or writing ad-hoc SQL from scratch."
)


def skill_name(pack: IndustryPack) -> str:
    return f"{pack.slug}-analyst"[:64]


def _kpi_catalog_markdown(kpi_defs: KpiDefsFile) -> str:
    lines = [
        f"- `{kpi.id}` - **{kpi.label}**{' (AI-suggested)' if kpi.source == 'agent_proposed' else ''}: "
        f"{kpi.description} (unit: {kpi.unit})"
        for kpi in kpi_defs.kpis
    ]
    if kpi_defs.skipped:
        lines.append("")
        lines.append("Not available for this data source:")
        lines.extend(f"- `{kpi_id}`: {reason}" for kpi_id, reason in kpi_defs.skipped.items())
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


def _render_understanding_sections(data_understanding: dict | None) -> str:
    """Render DataUnderstanding into skill markdown. Only when non-empty — a
    no-understanding run stays byte-identical for cassette stability."""
    if not data_understanding:
        return ""
    parts: list[str] = []
    tables = data_understanding.get("tables") or []
    columns = data_understanding.get("columns") or []
    business_questions = data_understanding.get("business_questions") or []
    open_questions = data_understanding.get("open_questions") or []
    glossary = data_understanding.get("glossary") or {}

    if tables:
        parts.append("## About Your Data\n")
        for t in tables[:5]:
            grain = (t.get("grain") or {}).get("description") or "unknown grain"
            temporal = t.get("temporal") or {}
            span = temporal.get("span")
            gran = temporal.get("granularity")
            gaps = temporal.get("gaps") or []
            line = f"- **{t['name']}**: {t.get('row_count', '?')} rows, {grain}."
            if span:
                line += f" Temporal coverage: {span}."
                if gran:
                    line += f" Granularity: {gran}."
                if gaps:
                    line += f" Gaps: {', '.join(gaps)}."
            parts.append(line)
        parts.append("")

    # Vocabularies for status/enum columns
    vocab_cols = [c for c in columns if c.get("vocabulary") and c.get("understanding_role") in ("status", "dimension")]
    if vocab_cols:
        parts.append("## Key Vocabularies\n")
        for c in vocab_cols[:5]:
            vocab = c.get("vocabulary") or []
            vals = ", ".join(f"{v['value']!r} ({v['count']})" for v in vocab[:5])
            parts.append(f"- **{c['table']}.{c['name']}** ({c.get('understanding_role')}): {vals}")
            # Add mandatory filter hint for status columns
            if c.get("understanding_role") == "status" and c.get("name") in ("status", "payment_status", "transaction_status"):
                parts.append(f"  - Note: when querying revenue, filter out cancelled/no-show statuses via `WHERE \"{c['name']}\" NOT IN ('Cancelled','no_show')` unless the user explicitly asks for them.")
        parts.append("")

    if business_questions:
        parts.append("## What You Can Ask (validated)\n")
        for q in business_questions[:6]:
            sketch = q.get("sql_sketch") or ""
            # Keep sketch short for skill readability
            sketch_short = sketch[:120] + ("..." if len(sketch) > 120 else "")
            parts.append(f"- **{q['question']}** (support {q.get('support', 0):.0%})")
            if sketch_short:
                parts.append(f"  - Example: `{sketch_short}`")
        parts.append("")

    caveats: list[str] = []
    for t in tables[:3]:
        for issue in (t.get("quality_issues") or [])[:2]:
            caveats.append(f"- {t['name']}: {issue}")
    for q in open_questions[:3]:
        col = q.get("column") or "unknown"
        caveats.append(f"- Open question on {col}: {q.get('question','')} (needs human review)")
    if caveats:
        parts.append("## Data Caveats\n")
        parts.extend(caveats)
        parts.append("")

    # Glossary excerpt (first 3)
    if glossary:
        parts.append("## Column Glossary (excerpt)\n")
        for k, v in list(glossary.items())[:3]:
            parts.append(f"- **{k}**: {v}")
        parts.append("")
        parts.append(f"_Full glossary and fingerprint details are in `config/data-understanding.json`._\n")

    return "\n".join(parts).strip()


def generate_skill(
    pack: IndustryPack,
    kpi_defs: KpiDefsFile,
    provider: LLMProvider | None = None,
    data_context: dict | None = None,
    data_understanding: dict | None = None,
) -> tuple[SkillFrontmatter, str]:
    """Return (frontmatter, body) for `skills/<name>/SKILL.md`.

    `data_context` (DataReview.to_context) is appended to the body ONLY when
    non-empty - a no-context run produces a byte-identical prompt AND output
    to before this feature existed, which is what keeps the LLM cassettes
    hitting on CI."""
    intro = _generate_intro(pack, provider)
    catalog = _kpi_catalog_markdown(kpi_defs)
    guardrail_lines = "\n".join(f"- {note}" for note in pack.guardrails.notes) or (
        "- Never request, display, or infer personally identifiable information."
    )
    context_block = render_data_context(data_context)
    context_section = f"\n\n## Context from the business owner\n\n{context_block}" if context_block else ""
    understanding_section = _render_understanding_sections(data_understanding)
    understanding_block = f"\n\n{understanding_section}\n" if understanding_section else ""
    body = (
        f"# {pack.name} Analyst\n\n"
        f"{intro}\n\n"
        "## Available KPIs\n\n"
        f"{catalog}\n\n"
        f"{understanding_block}"
        "## How to use the MCP tools\n\n"
        f"1. Call `{mcp_tool_ref('describe_schema')}` or `{mcp_tool_ref('list_kpis')}` first if "
        "you're unsure what's available.\n"
        f"2. Prefer `{mcp_tool_ref('get_kpi')}` with one of the KPI ids above over writing SQL.\n"
        f"3. Use `{mcp_tool_ref('run_safe_query')}` only for questions no KPI answers, and always "
        "as a read-only SELECT with explicit columns.\n\n"
        "## Guardrails\n\n"
        f"{guardrail_lines}\n"
        f"{context_section}\n"
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
