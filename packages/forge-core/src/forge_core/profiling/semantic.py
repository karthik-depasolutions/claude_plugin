"""Layer 2 — LLM semantic exploration (architecture doc §4.2, Layer 2).

Every semantic claim is evidence-attached and confidence-scored, and none of
it is trusted until the validation harness's fact-check pass re-verifies it
against the StructuralProfile. This module also enforces the privacy
boundary called out in the doc's Security Architecture section: PII-flagged
column values are redacted before anything leaves the process.
"""

from __future__ import annotations

import json
import os
from typing import Callable

from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import SemanticProfile, StructuralProfile

MAX_SAMPLE_ROWS_PER_TABLE = 3
REDACTED = "[REDACTED-PII]"


def _redacted_samples(data_source: DataSource, structural: StructuralProfile) -> dict[str, list[dict]]:
    pii_by_table: dict[str, set[str]] = {}
    for col in structural.columns:
        if col.is_likely_pii:
            pii_by_table.setdefault(col.table, set()).add(col.name)

    out: dict[str, list[dict]] = {}
    for table in data_source.tables:
        pii_cols = pii_by_table.get(table.name, set())
        rows = []
        for row in table.sample_rows[:MAX_SAMPLE_ROWS_PER_TABLE]:
            rows.append({k: (REDACTED if k in pii_cols else v) for k, v in row.items()})
        out[table.name] = rows
    return out


def _compact_structural(structural: StructuralProfile) -> list[dict]:
    return [
        {
            "table": c.table,
            "column": c.name,
            "dtype": c.dtype,
            "guessed_role": c.guessed_role.value,
            "null_percent": c.null_percent,
            "cardinality": c.cardinality,
            "is_likely_pii": c.is_likely_pii,
        }
        for c in structural.columns
    ]


_PROMPT_TEMPLATE = """You are profiling a customer's business data (possibly multiple tables).
You are given deterministically computed structural facts and a few redacted sample rows —
PII-flagged fields are already replaced with "{redacted}" before reaching you. Never assume you
know a redacted value.

STRUCTURAL FACTS (ground truth — every table/column reference you use below MUST come from here):
{structural}

SAMPLE ROWS (redacted, at most {max_rows} rows per table):
{samples}

Propose:
1. column_semantics — a plain-English meaning for ambiguous columns (skip obvious ones like an
   "email" column literally named email).
2. candidate_insights — 2-4 actionable analytical patterns or KPI ideas this data would support,
   each grounded in real table/column names above.
3. data_quality_flags — anything that looks off (missing values, suspicious cardinality, mixed
   units, orphaned foreign keys, inconsistent categories).
4. likely_central_entities — the 1-3 tables that represent the core business entities/events
   (e.g. the main transaction or fact table).

Return ONLY JSON with this exact shape:
{{
  "column_semantics": [
    {{"table": "...", "column": "...", "proposed_meaning": "...", "confidence": 0.0}}
  ],
  "candidate_insights": [
    {{"insight": "...", "confidence": 0.0, "tables": ["..."], "columns": ["..."],
      "suggested_kpi_name": "snake_case_or_null"}}
  ],
  "data_quality_flags": [
    {{"issue": "...", "severity": "low|medium|high", "tables": ["..."], "columns": ["..."]}}
  ],
  "likely_central_entities": ["table_name", ...]
}}

Rules: only reference tables/columns that appear in STRUCTURAL FACTS above. If you are unsure a
column exists, omit it rather than guessing. Return at most 6 column_semantics entries.
"""


def run_semantic_profile(
    data_source: DataSource,
    structural: StructuralProfile,
    provider: LLMProvider,
    *,
    use_agent: bool = False,
    packs: list[IndustryPack] | None = None,
    on_agent_stats: Callable[[dict], None] | None = None,
) -> SemanticProfile:
    """`use_agent=True` (with `packs` available) routes through
    `agentic.data_agent`'s tool-using agent instead of this single-shot
    call - it can preview real column values and search unfamiliar business
    terms before committing to a column meaning or industry guess, at the
    cost of more LLM calls. `use_agent=False` (the default) keeps this cheap
    single call; `packs` being empty/None also falls back to it, since the
    agent needs real candidate industries to ground a guess in."""
    if use_agent and packs:
        from forge_core.agentic.data_agent import run_data_understanding_agent

        column_semantics, suggested_industry = run_data_understanding_agent(
            data_source, structural, packs, on_stats=on_agent_stats
        )
        return SemanticProfile(
            column_semantics=column_semantics,
            suggested_industry=suggested_industry,
            model_used=os.environ.get("FORGE_LLM_AGENT_MODEL", "gemini-2.5-flash"),
        )

    prompt = _PROMPT_TEMPLATE.format(
        redacted=REDACTED,
        structural=json.dumps(_compact_structural(structural), indent=2, default=str),
        samples=json.dumps(_redacted_samples(data_source, structural), indent=2, default=str),
        max_rows=MAX_SAMPLE_ROWS_PER_TABLE,
    )
    raw = provider.generate_json(prompt)
    inner = getattr(provider, "_wrapped", None)
    model_name = getattr(provider, "model", None) or getattr(inner, "model", None)
    return SemanticProfile(
        column_semantics=raw.get("column_semantics", []),
        candidate_insights=raw.get("candidate_insights", []),
        data_quality_flags=raw.get("data_quality_flags", []),
        likely_central_entities=raw.get("likely_central_entities", []),
        model_used=model_name,
        raw_response=raw,
    )
