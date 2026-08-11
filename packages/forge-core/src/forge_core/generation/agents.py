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


def generate_agent(
    pack: IndustryPack, kpi_defs: KpiDefsFile, provider: LLMProvider | None = None
) -> tuple[AgentFrontmatter, str]:
    """Return (frontmatter, body) for `agents/<name>.md`."""
    body = _generate_system_prompt(pack, kpi_defs, provider)
    frontmatter = AgentFrontmatter(
        name=agent_name(pack),
        description=f"Deep-dive {pack.name} business analysis grounded in real KPI data.",
        tools=[mcp_tool_ref(t) for t in TOOL_NAMES],
        model="inherit",
    )
    return frontmatter, body
