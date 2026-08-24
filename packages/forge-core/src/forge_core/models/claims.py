"""P2-05's output contract — structured, closed, non-executable. There is
deliberately no `filter_expr`, no `bucketing_expr`, no `sql` field anywhere
below. That is the exact hole `boringdata/boring-semantic-layer`'s public
demo leaves open (raw Python lambda strings, later `eval()`'d with no AST
whitelist) - closed here by omission at the schema level, not by validation
after the fact. See PHASE_2.md §0.3, "the hard rule."
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.metrics import AggOp

ColumnKind = Literal["identifier", "measure", "dimension", "time", "flag", "free_text"]


class ColumnClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    meaning: str = Field(description="Prose - for humans, never executed.")
    kind: ColumnKind
    unit: str | None = Field(default=None, description='"INR" | "count" | "percent" | "score" | ...')
    is_pii: bool = False
    valid_aggregations: list[AggOp] = Field(
        default_factory=list, description="Closed enum, not expressions."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(
        default_factory=list, description="Must cite real tool results - verified by V1 (P2-06)."
    )


class RelationClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    # Deliberately NO cardinality field - that is a FACT established by
    # check_relationship (agentic/investigation_tools.py), never asserted
    # by the model. See P2-06's V4 gate.


__all__ = ["ColumnClaim", "ColumnKind", "RelationClaim"]
