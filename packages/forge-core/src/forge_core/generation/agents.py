"""Stage 5b — a deep-dive analysis subagent, one per industry pack.

Only the system-prompt prose is LLM-authored; the tool allow-list, name, and
model are fixed deterministically so the agent can never be generated with
access to a tool the runtime doesn't actually expose.
"""

from __future__ import annotations

from forge_core.generation.constants import TOOL_NAMES, mcp_tool_ref
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.plugin_spec import AgentFrontmatter
from forge_core.models.quality import render_data_context

_FALLBACK_PROMPT_TEMPLATE = (
    "You are a senior {name} business analyst. Always ground answers in the data reachable "
    "through the bundled MCP tools; never fabricate numbers. Start with describe_schema or "
    "list_kpis when unsure what is available, prefer get_kpi for standard metrics, and fall "
    "back to a read-only run_safe_query SELECT only when no KPI covers the question. State "
    "the KPI id or query used alongside every number you report."
)


def agent_name(pack: IndustryPack) -> str:
    return f"{pack.slug}-analyst"


def _generate_system_prompt(
    pack: IndustryPack, kpi_defs: KpiDefsFile, provider: LLMProvider | None
) -> str:
    if provider is None:
        return _FALLBACK_PROMPT_TEMPLATE.format(name=pack.name)
    kpi_ids = ", ".join(k.id for k in kpi_defs.kpis)
    prompt = (
        "Write a system prompt (plain prose, no markdown headers, 4-8 sentences) for an AI "
        f"subagent that performs deep-dive {pack.name} business analysis. It must:\n"
        f"- Only fetch data via these MCP tools: {', '.join(TOOL_NAMES)}.\n"
        f"- Prefer get_kpi for these pre-validated KPI ids when relevant: {kpi_ids}.\n"
        "- Never fabricate a number that isn't backed by a tool call result.\n"
        "- Always cite which KPI id or query produced each number.\n"
        "Do not invent example numbers or company names."
    )
    try:
        text = provider.generate_text(prompt).strip()
        return text or _FALLBACK_PROMPT_TEMPLATE.format(name=pack.name)
    except LLMError:
        return _FALLBACK_PROMPT_TEMPLATE.format(name=pack.name)


def _render_understanding_for_agent(data_understanding: dict | None) -> str:
    if not data_understanding:
        return ""
    parts: list[str] = []
    tables = data_understanding.get("tables") or []
    if tables:
        parts.append("Data overview:")
        for t in tables[:3]:
            grain = (t.get("grain") or {}).get("description") or "unknown grain"
            temporal = t.get("temporal") or {}
            span = temporal.get("span")
            parts.append(f"- {t['name']}: {t.get('row_count', '?')} rows, {grain}" + (f", span {span}" if span else ""))
    columns = data_understanding.get("columns") or []
    vocab_cols = [c for c in columns if c.get("vocabulary")]
    if vocab_cols:
        parts.append("Key vocabularies:")
        for c in vocab_cols[:3]:
            vals = ", ".join(v["value"] for v in (c.get("vocabulary") or [])[:3])
            parts.append(f"- {c['table']}.{c['name']}: {vals}")
    glossary = data_understanding.get("glossary") or {}
    if glossary:
        parts.append("Column meanings:")
        for k, v in list(glossary.items())[:3]:
            parts.append(f"- {k}: {v}")
    bqs = data_understanding.get("business_questions") or []
    if bqs:
        parts.append("Validated questions you can answer:")
        for q in bqs[:3]:
            parts.append(f"- {q['question']}")
    open_qs = data_understanding.get("open_questions") or []
    if open_qs:
        parts.append("Open questions (needs human review, do not guess):")
        for q in open_qs[:2]:
            parts.append(f"- {q.get('column')}: {q.get('question')}")
    return "\n".join(parts)


def generate_agent(
    pack: IndustryPack,
    kpi_defs: KpiDefsFile,
    provider: LLMProvider | None = None,
    data_context: dict | None = None,
    data_understanding: dict | None = None,
) -> tuple[AgentFrontmatter, str]:
    """Return (frontmatter, body) for `agents/<name>.md`.

    `data_context` is appended to the system prompt only when non-empty, so a
    no-context run's prompt AND output stay byte-identical (LLM cassettes
    keep hitting - the data never reaches the LLM call itself)."""
    body = _generate_system_prompt(pack, kpi_defs, provider)
    context_block = render_data_context(data_context)
    if context_block:
        body += f"\n\nContext from the business owner about this data:\n{context_block}"
    understanding_block = _render_understanding_for_agent(data_understanding)
    if understanding_block:
        body += f"\n\nData understanding for this customer's data:\n{understanding_block}"
    frontmatter = AgentFrontmatter(
        name=agent_name(pack),
        description=f"Deep-dive {pack.name} business analysis grounded in real KPI data.",
        tools=[mcp_tool_ref(t) for t in TOOL_NAMES],
        model="inherit",
    )
    return frontmatter, body
