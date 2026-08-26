"""Stage 4a — the schema binding layer.

This is the piece the original POC was missing (see plan §1/§2). It is the
single artifact that turns an industry-wide, canonical-role KPI definition
into something that can run against one specific customer's real columns.
Shipped inside every generated plugin as `config/schema_bindings.json` and
consumed only by the generic MCP runtime — never by an LLM at request time.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TableBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(description="Canonical table alias from the industry pack, e.g. 'fact'.")
    physical: str = Field(description="Real table/view name in the customer's DataSource.")
    grain: str = ""


class ColumnBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(description="Canonical role from the industry pack, e.g. 'revenue_amount'.")
    table_alias: str
    physical: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""
    sql_expression: str | None = Field(
        default=None,
        description="SQL to substitute for this role instead of the plain quoted column. Set "
        "only when the raw column can't be used as-is - today that means a date stored as "
        "non-ISO text, where the expression is STRPTIME(col, fmt). DuckDB's CAST raises rather "
        "than returning NULL on such a value, so without this a single DD-MM-YYYY column fails "
        "the entire build at dry-run. None means use the column directly.",
    )
    source: str = Field(
        default="deterministic",
        description="'deterministic' | 'llm_proposed' | 'agent_proposed' | 'human_override'.",
    )
    needs_confirmation: bool = Field(
        default=False,
        description="True when no resolution tier reached MIN_CONFIDENCE_RESOLVED - the best "
        "candidate found still shipped (so a KPI that doesn't need it never blocks on it), but "
        "binding/gate.py routes it to a human question if a shipped KPI actually depends on it. "
        "Never true for source='human_override' - a confirmed binding is never re-asked.",
    )
    alternatives: list[tuple[str, float]] = Field(
        default_factory=list,
        description="Runner-up (physical_column, confidence) pairs from the deterministic scorer, "
        "shown alongside the top pick when needs_confirmation - lets a human pick a different "
        "column instead of only confirming or rejecting the guess.",
    )


class ValueSetBinding(BaseModel):
    """Resolves a pack-defined logical value set (e.g. 'what counts as
    completed') to the customer's actual category strings."""

    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[str]
    source: str = "deterministic"


class SchemaBindings(BaseModel):
    """The complete binding artifact for one customer + one industry pack."""

    model_config = ConfigDict(extra="forbid")

    pack_slug: str
    data_source_id: str
    tables: list[TableBinding]
    columns: list[ColumnBinding]
    value_sets: list[ValueSetBinding] = Field(default_factory=list)
    allowed_tables: list[str]
    denied_columns: list[str] = Field(
        default_factory=list, description="Physical columns the runtime must never project."
    )
    unresolved_roles: list[str] = Field(
        default_factory=list, description="Canonical roles no candidate column could satisfy."
    )

    def table(self, alias: str) -> TableBinding:
        for t in self.tables:
            if t.alias == alias:
                return t
        raise KeyError(f"No table binding for alias {alias!r}")

    def column(self, role: str) -> ColumnBinding | None:
        for c in self.columns:
            if c.role == role:
                return c
        return None

    def value_set(self, name: str) -> ValueSetBinding | None:
        for v in self.value_sets:
            if v.name == name:
                return v
        return None


class BindingQuestion(BaseModel):
    """One low-confidence binding a shipped KPI actually depends on - the
    output of binding/gate.py. Mirrors DataQuestion's id scheme
    (models/quality.py) so the pause-handling convention stays uniform: the
    caller answers by role in RunRecord.binding_confirmations, keyed the
    same way `data_answers` is keyed by DataQuestion.id."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="'binding:{role}' - stable across resumes.")
    role: str
    physical: str
    confidence: float
    evidence: str
    alternatives: list[tuple[str, float]] = Field(default_factory=list)
    kpis_affected: list[str] = Field(default_factory=list)
    question: str
