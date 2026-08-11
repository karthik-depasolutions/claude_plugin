"""Stage 4 — GENERATE (four Gemini calls)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.gemini_client import generate_json, generate_text


def _context(schema_profile: dict, industry_pack: dict) -> str:
    return f"""SCHEMA PROFILE:
{json.dumps(schema_profile, indent=2, default=str)}

INDUSTRY PACK:
{json.dumps(industry_pack, indent=2)}

CRITICAL: Only reference column and table names that appear in the schema profile above.
Table name is "{industry_pack.get('table_name', 'bookings')}".
If you need a column that doesn't exist, say so instead of inventing one.
Never expose phone or customer_name in aggregate outputs (industry guardrail).
"""


def generate_skill(schema_profile: dict, industry_pack: dict) -> str:
    prompt = f"""Write a Claude Agent Skill (SKILL.md body only, no frontmatter yet).

{_context(schema_profile, industry_pack)}

Return JSON:
{{
  "skill_name": "booking-analyst",
  "description": "one-line trigger description for frontmatter",
  "markdown_body": "full markdown content after frontmatter — when to use, tool workflow, examples"
}}

The skill must instruct Claude to:
- Call describe_schema first if not already done
- Prefer get_kpi for standard KPIs from the industry pack
- Use run_safe_query only for ad-hoc questions, explaining the query
- Include 2-3 example user questions with approach
"""
    result = generate_json(prompt)
    desc = result.get("description", "Analyze diagnostic lab booking data")
    body = result.get("markdown_body", "")
    return (
        f"---\nname: {result.get('skill_name', 'booking-analyst')}\n"
        f"description: {desc}\n---\n\n{body}"
    )


def generate_tool_specs(schema_profile: dict, industry_pack: dict) -> dict:
    prompt = f"""Design MCP tool specifications for this bookings dataset.

{_context(schema_profile, industry_pack)}

Return JSON:
{{
  "tools": [
    {{
      "name": "describe_schema",
      "type": "builtin",
      "description": "..."
    }},
    {{
      "name": "run_safe_query",
      "type": "builtin",
      "description": "...",
      "parameters": [{{"name": "sql", "type": "string"}}],
      "constraints": ["SELECT only", "max 200 rows"]
    }},
    {{
      "name": "get_kpi",
      "type": "kpi_router",
      "description": "...",
      "parameters": [{{"name": "kpi_name", "type": "string"}}],
      "kpis": [
        {{
          "name": "snake_case",
          "label": "Human label",
          "formula": "plain English formula",
          "columns": ["col1"],
          "computation_hint": "pandas steps to compute"
        }}
      ]
    }}
  ]
}}

Always include describe_schema and run_safe_query.
Include get_kpi with one entry per relevant KPI from the industry pack KPI list.
You MAY add 1-2 new KPIs based on candidate_insights in the schema profile if they use real columns only.
"""
    return generate_json(prompt)


def generate_recipe(schema_profile: dict, industry_pack: dict, tool_specs: dict) -> tuple[str, str]:
    prompt = f"""Write a Claude Code slash command (markdown recipe) that orchestrates a report.

{_context(schema_profile, industry_pack)}

Available tools:
{json.dumps(tool_specs, indent=2)}

Return JSON:
{{
  "command_filename": "monthly-report.md",
  "description": "one-line for frontmatter",
  "markdown_body": "full command instructions — which get_kpi calls to make, narrative summary, reference dashboard artifact"
}}

The recipe should call 2-4 KPI tools and produce a written summary plus reference an HTML dashboard.
"""
    result = generate_json(prompt)
    filename = result.get("command_filename", "monthly-report.md")
    desc = result.get("description", "Generate booking analytics report")
    body = result.get("markdown_body", "")
    content = f"---\ndescription: {desc}\n---\n\n{body}"
    return filename, content


def generate_artifact(schema_profile: dict, industry_pack: dict, tool_specs: dict) -> str:
    kpis = []
    for tool in tool_specs.get("tools", []):
        if tool.get("name") == "get_kpi":
            kpis = tool.get("kpis", [])
            break

    prompt = f"""Create a self-contained HTML dashboard for booking analytics.

{_context(schema_profile, industry_pack)}

KPIs to visualize:
{json.dumps(kpis, indent=2)}

Return JSON:
{{
  "title": "Dashboard title",
  "html": "<!DOCTYPE html>... full HTML with Chart.js from CDN, bar chart placeholder for monthly revenue, table for city breakdown, metric cards. Use placeholder data attributes or JS comments indicating which get_kpi feeds each section — the packaged plugin will inject real values at runtime via MCP, but this template shows layout."
}}

Use Chart.js from cdn.jsdelivr.net. Plain HTML/CSS/JS only. Do not include phone or customer_name.
"""
    result = generate_json(prompt)
    return result.get("html", "<html><body><h1>Dashboard</h1></body></html>")


def run_generation(
    schema_profile: dict,
    industry_pack: dict,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("  [4a] Generating SKILL.md...")
    skill_md = generate_skill(schema_profile, industry_pack)
    (output_dir / "skill.md").write_text(skill_md, encoding="utf-8")

    print("  [4b] Generating tool specs...")
    tool_specs = generate_tool_specs(schema_profile, industry_pack)
    (output_dir / "tool_specs.json").write_text(
        json.dumps(tool_specs, indent=2), encoding="utf-8"
    )

    print("  [4c] Generating recipe...")
    recipe_name, recipe_md = generate_recipe(schema_profile, industry_pack, tool_specs)
    (output_dir / recipe_name).write_text(recipe_md, encoding="utf-8")

    print("  [4d] Generating artifact HTML...")
    artifact_html = generate_artifact(schema_profile, industry_pack, tool_specs)
    (output_dir / "dashboard.html").write_text(artifact_html, encoding="utf-8")

    return {
        "skill_md": skill_md,
        "tool_specs": tool_specs,
        "recipe_filename": recipe_name,
        "recipe_md": recipe_md,
        "artifact_html": artifact_html,
    }
