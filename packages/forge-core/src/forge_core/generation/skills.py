"""Stage 5a — SKILL.md generation.

Generates a modular multi-skill suite for the packaged plugin. All three skill names are
namespaced with the industry pack's slug so two generated plugins installed side by side in the
same Claude Code environment never collide on skill name:
1. Domain Analyst Skill (`<pack-slug>-analyst`): Grounded KPIs, schema mappings, MCP tool usage,
   and senior analytical diagnostic workflows.
2. Data Visualizer Skill (`<pack-slug>-data-visualizer`): Anthropic & Edward Tufte publication-quality
   chart selection trees, action titles, data-ink ratio principles, and colorblind-safe guidelines.
3. Root Cause Investigator Skill (`<pack-slug>-root-cause-investigator`): Systematic MECE metric
   decomposition, anomaly timeline detection, dimensional drill-down ranking, and Simpson's paradox
   safeguards.

The KPI catalog and guardrail notes are rendered deterministically straight from the compiled
artifacts (never hallucinated); the framing intro paragraph is optionally LLM-authored.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge_core.generation.constants import mcp_tool_ref
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.plugin_spec import SkillFrontmatter


@dataclass
class GeneratedSkill:
    name: str
    frontmatter: SkillFrontmatter
    body: str


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


def generate_domain_skill(
    pack: IndustryPack, kpi_defs: KpiDefsFile, provider: LLMProvider | None = None
) -> tuple[SkillFrontmatter, str]:
    """Generate the primary domain analyst skill (`<pack-slug>-analyst`)."""
    intro = _generate_intro(pack, provider)
    catalog = _kpi_catalog_markdown(kpi_defs)
    guardrail_lines = "\n".join(f"- {note}" for note in pack.guardrails.notes) or (
        "- Never request, display, or infer personally identifiable information."
    )
    body = (
        f"# {pack.name} Analyst\n\n"
        f"{intro}\n\n"
        "## Analytical Methodology\n\n"
        "When analyzing business performance, follow this structured diagnostic protocol:\n"
        "1. **Frame the Question**: Restate ambiguous questions with specific metrics, time horizons, and baseline periods.\n"
        "2. **Decompose Metrics First**: Break composite metrics into constituent drivers (e.g. `Revenue = Volume × Unit Price × Mix`).\n"
        "3. **Query via Grounded Tools**: Fetch data using the bundled MCP tools (`get_kpi`, `run_safe_query`) without guessing columns.\n"
        "4. **Drill Down by Dimensions**: Compare period-over-period performance across available segments, ordering by absolute variance contribution.\n"
        "5. **Synthesize with BLUF**: Lead with the Bottom Line Up Front, follow with quantified evidence, and conclude with actionable recommendations.\n\n"
        "## Available KPIs\n\n"
        f"{catalog}\n\n"
        "## How to Use the MCP Analytics Tools\n\n"
        "Before writing any SQL, read the `schema://model` resource (per-table meaning, column "
        "definitions, enum decodes) and `schema://cookbook` (verified example queries for this "
        "exact database). Use `schema://relationships` for join keys. Then follow this hierarchy:\n"
        f"1. **Discovery**: Call `{mcp_tool_ref('describe_data')}`, `{mcp_tool_ref('list_business_concepts')}`, or `{mcp_tool_ref('list_kpis')}` first to discover available business entities and metrics.\n"
        f"2. **KPIs & Metrics**: Prefer `{mcp_tool_ref('get_kpi')}` for standard verified metrics, `{mcp_tool_ref('compare_kpi')}` for period comparisons, `{mcp_tool_ref('rank_entities')}` for top/bottom rankings, and `{mcp_tool_ref('breakdown_metric')}` for dimensional slices.\n"
        f"3. **Categorical Slices**: Use `{mcp_tool_ref('get_value_set')}` to discover distinct category values safely without exploratory SQL.\n"
        f"4. **Entity Records**: Use `{mcp_tool_ref('get_record')}`, `{mcp_tool_ref('search_records')}`, or `{mcp_tool_ref('get_related_records')}` for entity lookups.\n"
        f"5. **Escape Hatch**: Use `{mcp_tool_ref('run_safe_query')}` ONLY as a last resort when no semantic or metric tool can answer the question.\n\n"
        "## Guardrails & Privacy\n\n"
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


def generate_visualization_skill(pack: IndustryPack) -> tuple[SkillFrontmatter, str]:
    """Generate the publication-quality data visualization skill (`<pack-slug>-data-visualizer`)."""
    name = f"{pack.slug}-data-visualizer"[:64]
    description = (
        "Publication-quality data visualization guide. Use when designing charts, selecting visual "
        "types, creating presentation graphics, or formatting charts for executive stakeholders."
    )
    frontmatter = SkillFrontmatter(
        name=name,
        description=description,
        metadata={"category": "visualization", "standard": "tufte-anthropic"},
    )
    body = (
        "# Data Visualizer\n\n"
        "Design publication-grade, accessible, and high-signal data visualizations based on Edward Tufte "
        "and Anthropic visual standards.\n\n"
        "## Chart Selection Decision Tree\n\n"
        "Select chart types based strictly on the analytical relationship being communicated:\n"
        "- **Temporal Trends (Time Series)**: Use **Line Charts** with time strictly on the horizontal (X) axis.\n"
        "- **Discrete Category Comparison**: Use **Horizontal or Vertical Bar Charts** sorted by magnitude.\n"
        "- **Distribution & Skew**: Use **Histograms** (for continuous variables) or **Box Plots** (for multi-group dispersion).\n"
        "- **Part-to-Whole Composition**: Use **Stacked Bar Charts** or **100% Stacked Area**. Use Donut/Pie charts *only* if categories <= 3.\n"
        "- **Correlation / Relationship**: Use **Scatter Plots** with clearly labeled axes and trendlines.\n\n"
        "## Tufte Visual Hierarchy & Data-Ink Ratio\n\n"
        "1. **Action-Oriented Headlines**: Never write passive titles like `'Revenue by Region'`. Write the conclusion: `'North Region drives 54% of total Q2 revenue growth'`.\n"
        "2. **Eliminate Chartjunk**: Remove top and right box spines. Use soft, muted gridlines (`alpha=0.15`) or eliminate background grids entirely.\n"
        "3. **Direct Data Labeling**: Label key data points directly rather than forcing readers to cross-reference distant legends.\n"
        "4. **Accessibility & Color**: Use colorblind-safe palettes (Viridis, ColorBrewer). Use color purposefully for emphasis, keeping non-focal series in neutral grays.\n"
    )
    return frontmatter, body


def generate_root_cause_skill(pack: IndustryPack) -> tuple[SkillFrontmatter, str]:
    """Generate the root-cause & diagnostic investigation skill (`<pack-slug>-root-cause-investigator`)."""
    name = f"{pack.slug}-root-cause-investigator"[:64]
    description = (
        "Systematic investigation of business metric shifts, drops, and anomalies. Use when "
        "explaining unexpected KPI changes, conducting dimensional drill-downs, or building post-mortems."
    )
    frontmatter = SkillFrontmatter(
        name=name,
        description=description,
        metadata={"category": "diagnostics", "framework": "mece-rca"},
    )
    body = (
        "# Root Cause Investigator\n\n"
        "Systematically diagnose and explain unexpected metric movements, anomalies, and performance variances.\n\n"
        "## 6-Step Root Cause Investigation Protocol\n\n"
        "1. **Validate Statistical Significance**:\n"
        "   - Compare the metric shift against historical standard deviation (Z-score).\n"
        "   - If the shift is within +/- 1.5 standard deviations, document it as normal variance.\n\n"
        "2. **Pinpoint the Timeline**:\n"
        "   - Determine if the change is a **Step Change** (sudden drop at a specific timestamp, indicating a bug/outage/event) or **Gradual Drift** (structural market shift).\n\n"
        "3. **Decompose the Core Formula**:\n"
        "   - Break the metric into independent mathematical components before running dimensional queries:\n"
        "     $$\\Delta \\text{Revenue} = \\Delta (\\text{Transactions} \\times \\text{Avg Value})$$\n\n"
        "4. **Dimensional Drill-Down**:\n"
        "   - Segment before vs. after across all available dimensions (channel, product, geography, cohort).\n"
        "   - Rank segments by **absolute contribution to total variance** (focus on the 80/20 driver).\n\n"
        "5. **Simpson's Paradox Safeguard**:\n"
        "   - Verify that aggregate trends are not confounded by mix shifts across sub-populations.\n\n"
        "6. **Synthesize Executive Deliverable**:\n"
        "   - **BLUF**: What changed and by how much.\n"
        "   - **Primary Driver**: Quantified share of impact.\n"
        "   - **Action Plan**: Immediate containment (0-7 days) vs structural improvements (30 days).\n"
    )
    return frontmatter, body


def generate_all_skills(
    pack: IndustryPack, kpi_defs: KpiDefsFile, provider: LLMProvider | None = None
) -> list[GeneratedSkill]:
    """Generate the complete multi-skill suite for the plugin."""
    domain_fm, domain_body = generate_domain_skill(pack, kpi_defs, provider)
    viz_fm, viz_body = generate_visualization_skill(pack)
    rca_fm, rca_body = generate_root_cause_skill(pack)

    return [
        GeneratedSkill(name=domain_fm.name, frontmatter=domain_fm, body=domain_body),
        GeneratedSkill(name=viz_fm.name, frontmatter=viz_fm, body=viz_body),
        GeneratedSkill(name=rca_fm.name, frontmatter=rca_fm, body=rca_body),
    ]


def generate_skill(
    pack: IndustryPack, kpi_defs: KpiDefsFile, provider: LLMProvider | None = None
) -> tuple[SkillFrontmatter, str]:
    """Backwards-compatible wrapper returning the primary domain analyst skill."""
    return generate_domain_skill(pack, kpi_defs, provider)
