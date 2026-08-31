"""Stage 2b — the data-quality review. Deterministic findings (Layer 1 facts,
each with real evidence) plus questions derived from them, computed once
during PROFILE and persisted on `RunRecord` so a resumed run never
regenerates them — see forge_core.profiling.quality and
docs/architecture.md's pause/resume note in orchestrator.py.

Deliberately separate from `SemanticProfile.data_quality_flags`
(schema_profile.py) — that is an LLM *claim* with no evidence attached;
these are facts computed straight from the data, sortable and reproducible
across a replay. `DataReview.to_context` is the one place a finding+answer
pair turns into the payload shipped to the plugin (see packaging,
generation, and binding — each consumes the same dict).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.common import Severity


class ValueCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    count: int
    percent: float


class QualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="code:table.column - server-assigned, never LLM-invented.")
    code: str = Field(
        description="dominant_value | high_null | inconsistent_format | mixed_types | numeric_outlier | single_value"
    )
    severity: Severity
    table: str
    column: str
    summary: str = Field(description="Deterministic English with the real numbers, e.g. "
        "'85.2% of 12,431 non-null rows are \"unclear\"'.")
    top_values: list[ValueCount] = Field(default_factory=list, description="<= 5, count desc then value asc.")


class DataQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Equals the finding.id it's grounded in, 'general_notes', or 'biz:<slug>'.")
    question: str
    context: str = Field(default="", description="The finding summary, shown under the question.")
    kind: str = Field(
        default="quality",
        description="'quality' (grounded in a data-quality finding) or 'business' "
        "(a clarification about what the data means).",
    )


class DataReview(BaseModel):
    """The complete Stage 2b output. `findings` is always populated (or
    empty); `questions` is empty until an LLM (or the deterministic
    fallback) has phrased them - see profiling/quality.py."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    findings: list[QualityFinding] = Field(default_factory=list)
    questions: list[DataQuestion] = Field(default_factory=list)
    sampled_tables: list[str] = Field(
        default_factory=list,
        description="Tables analyzed from a seeded row sample rather than a full scan "
        "(row count over MAX_ROWS_FOR_FREQUENCY) - their finding numbers are estimates.",
    )

    def to_context(self, answers: dict[str, str]) -> dict[str, Any]:
        """The one payload threaded into describe_schema, the SessionStart
        hook, generation prompts, and binding - see profiling/quality.py's
        module docstring for why every consumer shares this one shape
        instead of building its own view of the review."""
        question_by_id = {q.id: q for q in self.questions}
        notes = [
            {"question": question_by_id[qid].question, "answer": answer}
            for qid, answer in answers.items()
            if answer.strip() and qid in question_by_id
        ]
        findings = [
            {"table": f.table, "column": f.column, "severity": f.severity.value, "summary": f.summary}
            for f in self.findings
        ]
        return {"notes": notes, "findings": findings}


__all__ = ["DataQuestion", "DataReview", "QualityFinding", "ValueCount"]
