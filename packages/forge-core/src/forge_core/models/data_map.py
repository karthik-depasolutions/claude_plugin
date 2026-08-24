"""P2-03 — the precomputed data map: everything a query can answer, in one
deterministic (no LLM) pass, compact enough to fit in a prompt. This is what
makes the agentic layer (P2-05) affordable — an agent asking a tool per
column on a 200-table warehouse is thousands of round trips; the map settles
~95% of columns by statistics alone and hands the agent a short work queue
(`ambiguous_columns`) for the rest. See `profiling/data_map.py` for how it's
built and `docs/adr/0001-*.md` for why there is exactly one fact entity.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.common import ColumnRole
from forge_core.models.entity_graph import EntityRole, JoinEdge


class ColumnMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dtype: str
    null_pct: float
    cardinality: int
    distinct_ratio: float
    min_value: str | None = None
    max_value: str | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    format_fingerprint: str | None = Field(
        default=None, description='"currency" | "iso_date" | "email" | "uuid" | "enum" | None.'
    )
    top_values: list[tuple[str, int]] = Field(
        default_factory=list, description="Up to 8 (value, count) pairs. Never populated for a "
        "PII column - redacted before the map is built, not after."
    )
    guessed_role: ColumnRole
    is_likely_pii: bool = False
    ambiguous: bool = Field(
        default=False,
        description="True when the deterministic signals disagree or are weak - the agent's "
        "work queue is exactly the set of columns with this flag set.",
    )


class EntityMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: EntityRole
    grain: str
    row_count: int
    columns: list[ColumnMapEntry] = Field(default_factory=list)


class DataMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[EntityMapEntry] = Field(default_factory=list)
    edges: list[JoinEdge] = Field(default_factory=list, description="Verified only.")
    ambiguous_columns: list[str] = Field(
        default_factory=list, description='"table.column" - the agent\'s work queue.'
    )

    def to_prompt(self, *, char_budget: int = 30_000) -> str:
        """Compact, token-budgeted rendering: full detail for ambiguous
        columns, one line each for the rest. Degrades by summarizing
        unambiguous columns further, never by dropping a table silently -
        see `profiling/data_map.py::render_prompt` for the actual budgeting
        logic (kept out of the model so it can be unit-tested standalone)."""
        from forge_core.profiling.data_map import render_prompt

        return render_prompt(self, char_budget=char_budget)


__all__ = ["ColumnMapEntry", "DataMap", "EntityMapEntry"]
