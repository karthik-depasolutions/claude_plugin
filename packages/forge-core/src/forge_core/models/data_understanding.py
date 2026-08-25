"""U1 — DataUnderstanding artifact: the single versioned, evidence-backed
description of what the data *means*.

Deterministic fields are populated for every run (even --no-llm).
LLM-enriched fields are optional and gated — unresolvable stays as
open_question, never a confident wrong claim.

This extends DataMap (models/data_map.py) — DataMap is the precomputed
evidence store; DataUnderstanding adds interpretation, narrative, and
the ranked business questions that make generated skills non-generic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.common import ColumnRole
from forge_core.models.data_map import ColumnMapEntry
from forge_core.models.schema_profile import ColumnProfile, RelationshipCandidate, TableGrain


class UnderstandingRole(StrEnum):
    MEASURE = "measure"
    DIMENSION = "dimension"
    IDENTIFIER = "identifier"
    TIMESTAMP = "timestamp"
    STATUS = "status"
    TEXT = "text"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(description="deterministic | llm | human | heuristic")
    description: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class ValueCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    count: int = Field(ge=0)


class TemporalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span: str | None = None
    granularity: str | None = None
    gaps: list[str] = Field(default_factory=list)


class BusinessQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    sql_sketch: str | None = None
    support: float = Field(ge=0.0, le=1.0, default=0.0)
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    column: str | None = None
    reason: str = ""


class DomainAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_slug: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    matched_roles: list[str] = Field(default_factory=list)
    unmatched_roles: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    generated_at: str
    source_fingerprint: str
    token_counts: dict[str, Any] | None = None


class ColumnUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    name: str
    dtype: str
    guessed_role: ColumnRole
    format_fingerprint: str | None = None
    null_pct: float = 0.0
    cardinality: int = 0
    distinct_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    min_value: str | None = None
    max_value: str | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    top_values: list[tuple[str, int]] = Field(default_factory=list)
    is_likely_pii: bool = False
    ambiguous: bool = False
    # Interpreted layer
    understanding_role: UnderstandingRole = UnderstandingRole.UNKNOWN
    business_name: str = ""
    description: str = ""
    unit: str | None = None
    vocabulary: list[ValueCount] | None = None
    sensitivity: str = Field(default="none", description="none | pii | phi | financial")
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    open_question: str | None = None
    # Full nested for audit trail (optional, may be large)
    physical: ColumnProfile | None = None
    map_entry: ColumnMapEntry | None = None


class TableUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    row_count: int = 0
    grain: TableGrain | None = None
    relationships: list[RelationshipCandidate] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    temporal: TemporalProfile | None = None
    natural_description: str = ""
    columns: list[ColumnUnderstanding] = Field(default_factory=list)


class DataUnderstanding(BaseModel):
    """The single artifact every downstream consumer reads."""

    model_config = ConfigDict(extra="forbid")

    source_fingerprint: str
    tables: list[TableUnderstanding] = Field(default_factory=list)
    columns: list[ColumnUnderstanding] = Field(default_factory=list)
    domain: DomainAssessment = Field(default_factory=DomainAssessment)
    business_questions: list[BusinessQuestion] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance


__all__ = [
    "BusinessQuestion",
    "ColumnUnderstanding",
    "DataUnderstanding",
    "DomainAssessment",
    "Evidence",
    "OpenQuestion",
    "Provenance",
    "TableUnderstanding",
    "TemporalProfile",
    "UnderstandingRole",
    "ValueCount",
]
