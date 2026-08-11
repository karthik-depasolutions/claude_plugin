"""Stage 2 — PROFILE output contract.

Two layers, kept structurally separate per the architecture doc:
  StructuralProfile — deterministic, no LLM, always available.
  SemanticProfile    — LLM-proposed, always evidence-attached, never trusted
                        blindly (every claim is re-checked in the validation
                        harness's fact-check pass).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    name: str
    dtype: str
    null_percent: float
    cardinality: int
    distinct_ratio: float = Field(ge=0.0, le=1.0)
    guessed_role: ColumnRole
    min_value: float | str | None = None
    max_value: float | str | None = None
    sample_values: list[str] = Field(default_factory=list)
    is_likely_identifier: bool = False
    is_likely_pii: bool = False


class RelationshipCandidate(BaseModel):
    """A candidate join / foreign-key relationship, deterministically inferred
    from name similarity and value-overlap sampling — never invented by an LLM."""

    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str


class TableGrain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    grain_columns: list[str]
    description: str
    confidence: float = Field(ge=0.0, le=1.0)


class StructuralProfile(BaseModel):
    """Layer 1 — deterministic. Must never depend on an LLM call."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnProfile]
    relationships: list[RelationshipCandidate] = Field(default_factory=list)
    grains: list[TableGrain] = Field(default_factory=list)

    def columns_for(self, table: str) -> list[ColumnProfile]:
        return [c for c in self.columns if c.table == table]


class ColumnSemantic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    proposed_meaning: str
    confidence: float = Field(ge=0.0, le=1.0)


class CandidateInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight: str
    confidence: float = Field(ge=0.0, le=1.0)
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    suggested_kpi_name: str | None = None


class DataQualityFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str
    severity: str
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)


class SemanticProfile(BaseModel):
    """Layer 2 — LLM-proposed. Every field here is a *claim*, not a fact."""

    model_config = ConfigDict(extra="forbid")

    column_semantics: list[ColumnSemantic] = Field(default_factory=list)
    candidate_insights: list[CandidateInsight] = Field(default_factory=list)
    data_quality_flags: list[DataQualityFlag] = Field(default_factory=list)
    likely_central_entities: list[str] = Field(default_factory=list)
    model_used: str | None = None
    raw_response: dict[str, Any] | None = Field(
        default=None, description="Unmodified LLM JSON, kept for audit/debugging."
    )


class SchemaProfile(BaseModel):
    """The complete Stage 2 output. This is the ground truth every later
    stage (classification, binding, generation, validation) checks against."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str
    structural: StructuralProfile
    semantic: SemanticProfile | None = None
    source: DataSource
