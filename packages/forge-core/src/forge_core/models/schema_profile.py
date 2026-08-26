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
from forge_core.models.data_map import DataMap
from forge_core.models.datasource import DataSource
from forge_core.models.entity_graph import EntityGraph


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
    temporal_format: str | None = None
    """`strptime` pattern for a DATE column stored as non-ISO text (e.g.
    "%d-%m-%Y" for "02-05-1993"). None means either not temporal, or ISO
    text/native date where a plain CAST already works.

    Load-bearing: DuckDB's `CAST('02-05-1993' AS TIMESTAMP)` *raises* rather
    than returning NULL, so a KPI that casts such a column fails the entire
    build at dry-run. Carrying the format lets every SQL site emit
    `strptime(col, fmt)` instead - see `temporal_sql_expression`."""


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
    entity_graph: EntityGraph | None = Field(
        default=None,
        description="P2-01 — entities classified fact/dimension/bridge, edges verified by real "
        "cardinality/overlap queries. None only for a single-table source, where there is "
        "nothing to graph.",
    )
    data_map: DataMap | None = Field(
        default=None,
        description="P2-03 — the agent's grounding context: percentiles, format fingerprints, "
        "top values (PII-redacted), and which columns are ambiguous. None only when profiling "
        "ran without a live connection.",
    )

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


class IndustryGuess(BaseModel):
    """An LLM's read of which industry pack fits this data, grounded in real
    column names/values rather than the deterministic classifier's
    name/structure-only signals (see classification/matcher.py). Purely
    advisory — shown alongside the deterministic ranked matches during the
    industry pause, never used to auto-select a pack."""

    model_config = ConfigDict(extra="forbid")

    pack_slug_guess: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class SemanticProfile(BaseModel):
    """Layer 2 — LLM-proposed. Every field here is a *claim*, not a fact."""

    model_config = ConfigDict(extra="forbid")

    column_semantics: list[ColumnSemantic] = Field(default_factory=list)
    candidate_insights: list[CandidateInsight] = Field(default_factory=list)
    data_quality_flags: list[DataQualityFlag] = Field(default_factory=list)
    likely_central_entities: list[str] = Field(default_factory=list)
    suggested_industry: IndustryGuess | None = None
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


def temporal_sql_expression(column: str, temporal_format: str | None, *, qualifier: str = "") -> str:
    """The SQL that turns a date column into a real timestamp.

    One helper because three places build this independently - compiled KPI
    SQL (`compiler/sql_render.py`), generated metrics
    (`compiler/metric_compiler.py`) and the shipped runtime
    (`mcp-runtime/engine/metric_query.py`) - and they must agree. If they
    drift, a metric works in one surface and raises in another.

    `qualifier`, when given, is a table name/alias to prefix.
    """
    reference = f'"{qualifier}"."{column}"' if qualifier else f'"{column}"'
    if not temporal_format:
        return reference
    escaped = temporal_format.replace("'", "''")
    return f"STRPTIME({reference}, '{escaped}')"
