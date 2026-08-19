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
    code: str = Field(description="dominant_value | high_null | inconsistent_format | mixed_types | single_value")
    severity: Severity
    table: str
    column: str
    summary: str = Field(description="Deterministic English with the real numbers, e.g. "
        "'85.2% of 12,431 non-null rows are \"unclear\"'.")
    top_values: list[ValueCount] = Field(default_factory=list, description="<= 5, count desc then value asc.")


class DataQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Equals the finding.id it's grounded in, or 'general_notes'.")
    question: str
    context: str = Field(default="", description="The finding summary, shown under the question.")


class DataReview(BaseModel):
    """The complete Stage 2b output. `findings` is always populated (or
    empty); `questions` is empty until an LLM (or the deterministic
    fallback) has phrased them - see profiling/quality.py."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    findings: list[QualityFinding] = Field(default_factory=list)
    questions: list[DataQuestion] = Field(default_factory=list)
    skipped_tables: list[str] = Field(
        default_factory=list, description="Tables too large for the frequency pass - see MAX_ROWS_FOR_FREQUENCY."
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


def render_data_context(context: dict[str, Any] | None, *, cap: int = 2000) -> str:
    """Render a `to_context()` payload into a compact markdown block for
    prompt injection / SessionStart hooks. Empty context (or empty notes and
    findings) renders to "" so callers can append only when non-empty - a
    no-context prompt stays byte-identical to before this feature existed.

    `cap` bounds the block for the SessionStart hook, which is injected into
    *every* session and has no length limit enforced anywhere today."""
    if not context:
        return ""
    lines: list[str] = []
    notes = context.get("notes") or []
    findings = context.get("findings") or []
    if notes:
        lines.append("## What the business owner told us")
        for note in notes:
            lines.append(f"- Q: {note['question']}  A: {note['answer']}")
    if findings:
        lines.append("## Known data-quality findings")
        for finding in findings:
            location = f"{finding['table']}.{finding['column']}"
            lines.append(f"- [{finding['severity']}] {location}: {finding['summary']}")
    text = "\n".join(lines)
    if cap and len(text) > cap:
        return text[:cap]
    return text


__all__ = ["DataQuestion", "DataReview", "QualityFinding", "ValueCount", "render_data_context"]
