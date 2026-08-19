"""Stage 5d — slash commands, one per generated Recipe."""

from __future__ import annotations

from forge_core.generation.constants import TOOL_NAMES, mcp_tool_ref
from forge_core.generation.recipes import Recipe, build_recipes
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.plugin_spec import CommandFrontmatter
from forge_core.models.quality import render_data_context

_FALLBACK_INTRO = "Generate a {title} using the pre-validated KPIs bundled with this plugin."


def _generate_intro(recipe: Recipe, pack: IndustryPack, provider: LLMProvider | None) -> str:
    if provider is None:
        return _FALLBACK_INTRO.format(title=recipe.title)
    prompt = (
        f"Write one short plain-prose sentence introducing a '{recipe.title}' report command for "
        f"{pack.name} business data. No markdown, no invented numbers."
    )
    try:
        text = provider.generate_text(prompt).strip()
        return text or _FALLBACK_INTRO.format(title=recipe.title)
    except LLMError:
        return _FALLBACK_INTRO.format(title=recipe.title)


def generate_command(
    recipe: Recipe,
    pack: IndustryPack,
    provider: LLMProvider | None = None,
    data_context: dict | None = None,
) -> tuple[str, CommandFrontmatter, str]:
    """Return (command_name, frontmatter, body) for `commands/<name>.md`.

    `data_context` is appended to the body only when non-empty - a no-context
    run's prompt AND output stay byte-identical (LLM cassettes keep hitting)."""
    intro = _generate_intro(recipe, pack, provider)
    steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(recipe.steps, start=1))
    context_block = render_data_context(data_context)
    context_section = f"\n\nContext from the business owner:\n{context_block}" if context_block else ""
    body = f"{intro}\n\n{steps_md}\n{context_section}\n"
    frontmatter = CommandFrontmatter(
        description=f"{recipe.title}: run every available KPI and summarize the results.",
        allowed_tools=[mcp_tool_ref(t) for t in TOOL_NAMES],
    )
    return recipe.id, frontmatter, body


def generate_commands(
    pack: IndustryPack,
    kpi_defs: KpiDefsFile,
    provider: LLMProvider | None = None,
    data_context: dict | None = None,
) -> list[tuple[str, CommandFrontmatter, str]]:
    return [generate_command(r, pack, provider, data_context) for r in build_recipes(pack, kpi_defs)]
