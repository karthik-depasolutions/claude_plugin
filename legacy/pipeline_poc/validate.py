"""Stage 5 — VALIDATE."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.gemini_client import generate_json
from pipeline.kpi_engine import dry_run_kpis

SQL_KEYWORDS = {
    "select", "from", "where", "group", "by", "order", "having", "and", "or",
    "not", "in", "as", "on", "join", "left", "right", "inner", "outer", "limit",
    "sum", "count", "avg", "min", "max", "round", "case", "when", "then", "else",
    "end", "distinct", "between", "like", "is", "null", "true", "false", "month",
    "year", "date", "cast", "coalesce", "over", "partition", "desc", "asc",
}


def _valid_columns(schema_profile: dict) -> set[str]:
    cols = schema_profile["structural_profile"]["columns"]
    return {c["name"].lower() for c in cols}


def _extract_column_refs(text: str) -> set[str]:
    tokens = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", text.lower()))
    return tokens - SQL_KEYWORDS - {"bookings", "get_kpi", "describe_schema", "run_safe_query"}


def fact_check_outputs(
    schema_profile: dict,
    generated: dict[str, Any],
) -> dict:
    valid = _valid_columns(schema_profile)
    issues: list[dict] = []

    # Only check explicit column lists from tool specs — not prose/HTML token scraping
    for tool in generated["tool_specs"].get("tools", []):
        for kpi in tool.get("kpis", []):
            bad = [col for col in kpi.get("columns", []) if col.lower() not in valid]
            if bad:
                issues.append({
                    "source": "tool_specs.json",
                    "unknown_columns": bad,
                    "context": f"KPI {kpi.get('name')}",
                })

    # Check skill/recipe for backtick-quoted column names only
    kpi_names = {
        k.get("name", "").lower()
        for tool in generated["tool_specs"].get("tools", [])
        for k in tool.get("kpis", [])
    }
    skip = SQL_KEYWORDS | kpi_names | {
        "bookings", "describe_schema", "run_safe_query", "get_kpi", "completed", "cancelled",
    }
    for source, text in [
        ("skill.md", generated["skill_md"]),
        (generated["recipe_filename"], generated["recipe_md"]),
    ]:
        quoted = set(re.findall(r"[`'\"]([a-z_][a-z0-9_]*)[`'\"]", text.lower()))
        unknown = sorted(c for c in quoted if c not in valid and c not in skip)
        if unknown:
            issues.append({"source": source, "unknown_columns": unknown})

    status = "fail" if issues else "pass"
    return {"check": "fact_check", "status": status, "issues": issues, "valid_columns": sorted(valid)}


def self_critique(schema_profile: dict, generated: dict[str, Any]) -> dict:
    prompt = f"""Review these auto-generated plugin artifacts for correctness.

SCHEMA PROFILE:
{json.dumps(schema_profile, indent=2, default=str)}

GENERATED SKILL (excerpt):
{generated['skill_md'][:3000]}

TOOL SPECS:
{json.dumps(generated['tool_specs'], indent=2)}

RECIPE:
{generated['recipe_md'][:2000]}

Return JSON:
{{
  "overall_assessment": "pass|warn|fail",
  "findings": [
    {{
      "severity": "error|warning|info",
      "location": "skill|tools|recipe|artifact",
      "issue": "description",
      "suggestion": "fix"
    }}
  ],
  "unsupported_references": ["column or claim that doesn't exist in schema"],
  "contradictions": ["any internal contradictions"]
}}

Be genuinely critical. Flag hallucinated columns, wrong formulas, or guardrail violations (phone/customer_name in aggregates).
"""
    critique = generate_json(prompt)
    status = critique.get("overall_assessment", "warn")
    return {"check": "self_critique", "status": status, "critique": critique}


def run_validation(
    df: pd.DataFrame,
    schema_profile: dict,
    generated: dict[str, Any],
) -> dict:
    print("  [5a] Fact-checking column references...")
    fact = fact_check_outputs(schema_profile, generated)

    print("  [5b] Self-critique pass (Gemini)...")
    critique = self_critique(schema_profile, generated)

    print("  [5c] Dry-running KPI computations...")
    kpi_runs = dry_run_kpis(df, generated["tool_specs"])
    kpi_fails = [k for k in kpi_runs if k["status"] == "fail"]
    dry_status = "fail" if kpi_fails else ("warn" if any(k["status"] == "warn" for k in kpi_runs) else "pass")

    report = {
        "fact_check": fact,
        "self_critique": critique,
        "dry_run": {"check": "dry_run", "status": dry_status, "kpi_results": kpi_runs},
    }

    hard_fails = (
        fact["status"] == "fail"
        or critique["status"] == "fail"
        or dry_status == "fail"
    )
    report["overall"] = "fail" if hard_fails else ("warn" if critique["status"] == "warn" or dry_status == "warn" else "pass")
    return report
