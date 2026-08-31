"""Deterministic in-process `LLMProvider` for tests and offline demos.

The understanding phase is mandatory (see `orchestrator.run_pipeline`), so
every pipeline test needs *some* provider. This one returns minimal,
schema-valid responses keyed off keywords in the prompt - no network, no
API key, no recorded cassette. For fidelity testing of the real
prompt/response loop, use the cassette system (`forge_core.llm.cassette`)
instead.
"""

from __future__ import annotations

import re
from typing import Any


class FakeLLMProvider:
    """Implements the `LLMProvider` protocol with canned, structurally-valid
    output. Extend `generate_json` as new prompt shapes are added."""

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
        lower = prompt.lower()
        tables = (
            re.findall(r'"table":\s*"([^"]+)"', prompt)
            or re.findall(r"^TABLE:\s*(\S+)", prompt, re.M)
            or re.findall(r"^(\w+): \w[\w ]*\|", prompt, re.M)  # compact-schema "table: col|role|..." lines
        )
        # semantic-profile prompts use '"column": "x"'; synthesis per-table
        # prompts use '- x | dtype=...' bullet lines.
        columns = re.findall(r'"column":\s*"([^"]+)"', prompt) or re.findall(
            r"^-\s+(\S+)\s+\|\s+dtype=", prompt, re.M
        )
        first_table = tables[0] if tables else "data"

        # semantic profile (profiling.semantic.run_semantic_profile).
        # `likely_central_entities` is deliberately left empty: a canned guess
        # here would override the deterministic fact-table scorer and mis-bind.
        if "candidate_insights" in lower and "likely_central_entities" in lower:
            return {
                "column_semantics": [],
                "candidate_insights": [
                    {
                        "insight": f"Volume of {first_table} records over time.",
                        "confidence": 0.6,
                        "tables": [first_table],
                        "columns": columns[:1],
                        "suggested_kpi_name": None,
                    }
                ],
                "data_quality_flags": [],
                "likely_central_entities": [],
            }

        # global synthesis (profiling.synthesis - checked before the per-table
        # shape because a serialized TableDoc leaks "purpose"/"columns" into
        # this prompt too).
        if '"cookbook"' in lower and '"caveats"' in lower:
            return {
                "overview": "A business dataset with one or more tables.",
                "caveats": [],
                "patterns": [],
                "cookbook": [],
            }

        # per-table semantic doc (profiling.synthesis)
        if '"grain_prose"' in lower and '"cookbook"' not in lower:
            return {
                "purpose": f"One row per record in {first_table}.",
                "role": "fact",
                "grain_prose": f"one row per {first_table} record",
                "columns": [
                    {"name": c, "meaning": f"{c} value.", "enum": None, "example": None, "confidence": "low"}
                    for c in dict.fromkeys(columns)
                ],
            }

        # self-critique (validation.self_critique)
        if '"findings"' in lower and "severity" in lower:
            return {"findings": []}

        # business-context clarification questions (profiling.quality). Default
        # to none so straight-through pipeline tests don't pause; a test that
        # exercises the clarification flow subclasses this to return some.
        if '"slug"' in lower and "value sets" in lower:
            return {"questions": []}

        # data-review question phrasing (profiling.quality)
        if '"questions"' in lower and "finding_id" in lower:
            return {"questions": []}

        return {}

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        lower = prompt.lower()
        if "html" in lower or "<div" in lower or "dashboard" in lower:
            return "<section><h1>KPI Snapshot</h1></section>"
        return "Use this skill for questions about this business's data and its KPIs."


__all__ = ["FakeLLMProvider"]
