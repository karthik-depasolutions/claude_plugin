"""Stage 2 — PROFILE output contract.

Two layers, kept structurally separate per the architecture doc:
  StructuralProfile — deterministic, no LLM, always available.
  SemanticProfile    — LLM-proposed, always evidence-attached, never trusted
                        blindly (every claim is re-checked in the validation
                        harness's fact-check pass).
"""

from __future__ import annotations

from typing import Any, Literal

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


class RelationshipCandidate(BaseModel):
    """A candidate join / foreign-key relationship, deterministically inferred
    from name similarity and value-overlap sampling — never invented by an LLM.

    `strength` is "strong" when nearly every child value resolves to a parent
    key, "weak" when most (but not all) do — a real relationship with dirty
    data, still worth surfacing. Absence of any relationship is normal and
    never an error."""

    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    confidence: float = Field(ge=0.0, le=1.0)
    strength: Literal["strong", "weak"] = "strong"
    evidence: str


class TableGrain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    grain_columns: list[str]
    description: str
    confidence: float = Field(ge=0.0, le=1.0)


class Correlation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column_a: str
    column_b: str
    pearson_r: float
    n: int


class TemporalPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    buckets: dict[str, int] = Field(description="ISO month -> row count, chronological.")
    trend: Literal["rising", "falling", "flat"]
    seasonal: bool
    day_of_week: dict[str, int] = Field(
        default_factory=dict, description="Weekday name -> row count (Mon..Sun)."
    )
    year_over_year: dict[str, float] = Field(
        default_factory=dict, description="Year -> growth ratio vs the prior year (1.0 = flat)."
    )


class FunctionalDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    determinant: str
    dependent: str


class RedundantColumns(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column_a: str
    column_b: str
    note: str


class DenormalizationMismatch(BaseModel):
    """A stored aggregate on the parent side of a relationship that disagrees
    with re-aggregating the child rows — e.g. orders.total != SUM(items)."""

    model_config = ConfigDict(extra="forbid")

    parent_table: str
    parent_column: str
    child_table: str
    child_expression: str
    mismatched_rows: int
    checked_rows: int
    example: str = ""


class Segment(BaseModel):
    """How a table's rows break down across one dimension — the natural
    slices a reader would want before writing a query."""

    model_config = ConfigDict(extra="forbid")

    table: str
    dimension: str
    top_groups: list[tuple[str, float]] = Field(
        description="(value, share-of-rows 0..1), largest first, at most 5."
    )
    concentration: Literal["high", "moderate", "even"]


class PatternsRaw(BaseModel):
    """Deterministic statistical patterns — raw numbers, no interpretation.
    Every list defaults empty; a dataset with no detectable pattern of a
    given kind is normal."""

    model_config = ConfigDict(extra="forbid")

    correlations: list[Correlation] = Field(default_factory=list)
    temporal: list[TemporalPattern] = Field(default_factory=list)
    functional_dependencies: list[FunctionalDependency] = Field(default_factory=list)
    redundancies: list[RedundantColumns] = Field(default_factory=list)
    mismatches: list[DenormalizationMismatch] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)


class StructuralProfile(BaseModel):
    """Layer 1 — deterministic. Must never depend on an LLM call."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnProfile]
    relationships: list[RelationshipCandidate] = Field(default_factory=list)
    grains: list[TableGrain] = Field(default_factory=list)
    value_sets: dict[str, list[str]] = Field(
        default_factory=dict,
        description='"table.column" -> full distinct value set, for low-cardinality columns.',
    )
    patterns: PatternsRaw = Field(default_factory=PatternsRaw)

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
