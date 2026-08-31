"""Layer 2 — LLM semantic exploration (architecture doc §4.2, Layer 2).

Every semantic claim is evidence-attached and confidence-scored, and none of
it is trusted until the validation harness's fact-check pass re-verifies it
against the StructuralProfile.
"""

from __future__ import annotations

import json

from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import SemanticProfile, StructuralProfile

MAX_SAMPLE_ROWS_PER_TABLE = 12


def _sample_rows(data_source: DataSource) -> dict[str, list[dict]]:
    return {
        table.name: list(table.sample_rows[:MAX_SAMPLE_ROWS_PER_TABLE])
        for table in data_source.tables
    }


def _compact_structural(structural: StructuralProfile) -> list[dict]:
    return [
        {
            "table": c.table,
            "column": c.name,
            "dtype": c.dtype,
            "guessed_role": c.guessed_role.value,
            "null_percent": c.null_percent,
            "cardinality": c.cardinality,
        }
        for c in structural.columns
    ]


_PROMPT_TEMPLATE = """You are profiling a customer's business data (possibly multiple tables).
You are given deterministically computed structural facts and a few real sample rows.

STRUCTURAL FACTS (ground truth — every table/column reference you use below MUST come from here):
{structural}

SAMPLE ROWS (at most {max_rows} rows per table):
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
) -> SemanticProfile:
    prompt = _PROMPT_TEMPLATE.format(
        structural=json.dumps(_compact_structural(structural), indent=2, default=str),
        samples=json.dumps(_sample_rows(data_source), indent=2, default=str),
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
