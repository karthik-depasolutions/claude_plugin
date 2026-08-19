"""Stage 5 - the generation engine.

Produces every plugin component from already-validated, compiled pipeline
output (`SchemaBindings`, `KpiDefsFile`). Gemini never writes executable
code here - it only ever contributes prose (skill/agent/command intros and
the agent system prompt) - and every fact those pieces cite (KPI ids,
labels, guardrail notes) is substituted in deterministically. `provider`
may be `None`, in which case every component falls back to deterministic
templated prose with no LLM call at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge_core.generation.agents import agent_name, generate_agent
from forge_core.generation.artifacts import generate_dashboard
from forge_core.generation.commands import generate_commands
from forge_core.generation.constants import MCP_SERVER_NAME, TOOL_NAMES, mcp_tool_ref
from forge_core.generation.hooks import generate_hooks
from forge_core.generation.skills import generate_skill, skill_name
from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.plugin_spec import (
    AgentFrontmatter,
    CommandFrontmatter,
    HooksFile,
    SkillFrontmatter,
)


@dataclass
class GeneratedCommand:
    name: str
    frontmatter: CommandFrontmatter
    body: str


@dataclass
class GeneratedPlugin:
    """Everything the packaging stage (M9) needs, already validated against
    the plugin-spec frontmatter models - packaging only serializes to disk."""

    skill_name: str
    skill_frontmatter: SkillFrontmatter
    skill_body: str
    agent_name: str
    agent_frontmatter: AgentFrontmatter
    agent_body: str
    commands: list[GeneratedCommand] = field(default_factory=list)
    hooks: HooksFile = field(default_factory=HooksFile)
    dashboard_html: str = ""


def generate_plugin_content(
    pack: IndustryPack,
    kpi_defs: KpiDefsFile,
    source: DataSource,
    provider: LLMProvider | None = None,
    data_context: dict | None = None,
) -> GeneratedPlugin:
    """Produce every plugin component. `data_context` (the DataReview
    to_context payload) is threaded to skills/agents/commands/hooks - each
    consumer appends it to its output ONLY when non-empty, so a no-context
    run stays byte-identical and the LLM cassettes keep hitting."""
    skill_fm, skill_body = generate_skill(pack, kpi_defs, provider, data_context)
    agent_fm, agent_body = generate_agent(pack, kpi_defs, provider, data_context)
    commands = [
        GeneratedCommand(name=name, frontmatter=fm, body=body)
        for name, fm, body in generate_commands(pack, kpi_defs, provider, data_context)
    ]

    return GeneratedPlugin(
        skill_name=skill_name(pack),
        skill_frontmatter=skill_fm,
        skill_body=skill_body,
        agent_name=agent_name(pack),
        agent_frontmatter=agent_fm,
        agent_body=agent_body,
        commands=commands,
        hooks=generate_hooks(pack, data_context),
        dashboard_html=generate_dashboard(pack.name, kpi_defs, source),
    )


__all__ = [
    "MCP_SERVER_NAME",
    "TOOL_NAMES",
    "GeneratedCommand",
    "GeneratedPlugin",
    "generate_plugin_content",
    "mcp_tool_ref",
]
