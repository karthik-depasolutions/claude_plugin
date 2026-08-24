"""Shared building blocks for P2-04's investigation tools and P2-07's
parameterized metric layer. `AggOp` is the one design decision worth
stealing verbatim from `boringdata/boring-semantic-layer`'s public demo
(see PHASE_2.md's own calibration table): a closed aggregation enum is the
whole reason a numeric rollup can be one tool/field instead of nine, and the
reason it can never become an injection surface. Every numeric rollup
anywhere in Phase 2 - in tools, in metric definitions, in agent output -
uses this enum. No exceptions, no "custom" member, no escape hatch.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.entity_graph import JoinEdge


class AggOp(str, Enum):
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    NUNIQUE = "nunique"
    STD = "std"
    VAR = "var"
    MEDIAN = "median"


_SQL_FUNCTION: dict[AggOp, str] = {
    AggOp.SUM: "SUM",
    AggOp.MEAN: "AVG",
    AggOp.MIN: "MIN",
    AggOp.MAX: "MAX",
    AggOp.COUNT: "COUNT",
    AggOp.NUNIQUE: "COUNT DISTINCT",  # rendered specially - see render_aggregation
    AggOp.STD: "STDDEV",
    AggOp.VAR: "VARIANCE",
    AggOp.MEDIAN: "MEDIAN",
}


def render_aggregation(op: AggOp, quoted_column: str) -> str:
    """The only place an AggOp becomes SQL text - never string-formatted
    ad hoc at a call site, so there is exactly one function to audit for
    "does this enum member map to safe, parameterless SQL"."""
    if op == AggOp.NUNIQUE:
        return f"COUNT(DISTINCT {quoted_column})"
    return f"{_SQL_FUNCTION[op]}({quoted_column})"


class FilterOp(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"


_SQL_FILTER_OP: dict[FilterOp, str] = {
    FilterOp.EQ: "=",
    FilterOp.NEQ: "!=",
    FilterOp.GT: ">",
    FilterOp.GTE: ">=",
    FilterOp.LT: "<",
    FilterOp.LTE: "<=",
}


TimeGrain = Literal["day", "week", "month", "quarter", "year"]


class FilterSpec(BaseModel):
    """A structured predicate - never an expression string. `values` holds
    one element for eq/neq/gt/gte/lt/lte, one-or-more for in/not_in."""

    model_config = ConfigDict(extra="forbid")

    column: str
    op: FilterOp
    values: list[Any] = Field(min_length=1)


class DimensionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str = Field(description='"table.column", e.g. "courses.course_name".')
    table: str
    physical: str
    join_path: list[JoinEdge] = Field(default_factory=list, description="[] when on the base entity.")
    cardinality: int
    fan_out_safe: bool


class MetricDefinition(BaseModel):
    """P2-07 — replaces a frozen `CompiledKpi` SQL string with a
    parameterized definition the runtime renders at query time. Every field
    is closed/structured; `aggregation` is `AggOp`, `default_filters` is
    `list[FilterSpec]` - neither can carry executable content (PHASE_2.md
    §0.3, "the hard rule")."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    base_entity: str
    measure_column: str
    aggregation: AggOp
    unit: str
    allowed_dimensions: list[DimensionRef] = Field(default_factory=list)
    allowed_time_grains: list[TimeGrain] = Field(default_factory=list)
    time_column: str | None = Field(
        default=None, description="The base entity's real time column, when allowed_time_grains is non-empty."
    )
    default_filters: list[FilterSpec] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list, description="Validated by P1-01's AST policy.")
    source: Literal["generated", "agent_proposed"] = "generated"


__all__ = [
    "AggOp",
    "DimensionRef",
    "FilterOp",
    "FilterSpec",
    "MetricDefinition",
    "TimeGrain",
    "render_aggregation",
]
