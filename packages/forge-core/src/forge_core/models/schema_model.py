"""The knowledge pack the generated plugin ships as `config/schema_model.json`
and the MCP runtime serves to any connecting client.

Produced by `forge_core.profiling.synthesis.build_schema_model` - a
mandatory LLM pass over the deterministic `StructuralProfile`. Every
table/column reference in here is fact-checked against the structural
profile before it is written, and every `cookbook` SQL string is executed
once against the real data (`verified`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
TableRole = Literal["fact", "dimension", "lookup", "junction", "log", "staging", "unknown"]


class ColumnDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    meaning: str
    enum: dict[str, str] | None = Field(
        default=None, description="Raw value -> decoded label, when the column is a coded enum."
    )
    example: str | None = None
    confidence: Confidence = "low"


class TableDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    purpose: str
    role: TableRole = "unknown"
    grain_prose: str = ""
    columns: list[ColumnDoc] = Field(default_factory=list)


class RelationshipDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_ref: str = Field(description='"table.column" on the child side.')
    to_ref: str = Field(description='"table.column" on the parent side.')
    strength: Literal["strong", "weak"] = "strong"
    cardinality: str = "N:1"


class PatternNote(BaseModel):
    """A statistical pattern turned into an actionable note - not a bare stat."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["temporal", "correlation", "dependency", "redundancy", "quality", "segment"]
    finding: str
    evidence: str = ""
    directive: str = ""
    affects: list[str] = Field(default_factory=list)


class CookbookEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    sql: str
    tables: list[str] = Field(default_factory=list)
    notes: str = ""
    verified: bool = False


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_hash: str
    generated_by: str
    overview: str = ""
    caveats: list[str] = Field(default_factory=list)
    tables: list[TableDoc] = Field(default_factory=list)
    relationships: list[RelationshipDoc] = Field(default_factory=list)
    patterns: list[PatternNote] = Field(
        default_factory=list, description="LLM-interpreted, actionable pattern notes."
    )
    value_sets: dict[str, list[str]] = Field(
        default_factory=dict,
        description='"table.column" -> full distinct value set, for low-cardinality columns.',
    )
    statistics: dict = Field(
        default_factory=dict,
        description="Raw deterministic findings: correlations, monthly temporal buckets, "
        "functional dependencies, redundant columns. The numbers behind `patterns`.",
    )
    quality_findings: list[dict] = Field(
        default_factory=list,
        description="Deterministic data-quality facts (dominant value, high null, mixed types, "
        "format drift, numeric outliers) — each with a table/column, severity, and summary.",
    )
    cookbook: list[CookbookEntry] = Field(default_factory=list)

    def table(self, name: str) -> TableDoc | None:
        return next((t for t in self.tables if t.name == name), None)
