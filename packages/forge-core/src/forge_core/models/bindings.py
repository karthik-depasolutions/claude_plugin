"""Stage 4a — the schema binding layer.

This is the piece the original POC was missing (see plan §1/§2). It is the
single artifact that turns an industry-wide, canonical-role KPI definition
into something that can run against one specific customer's real columns.
Shipped inside every generated plugin as `config/schema_bindings.json` and
consumed only by the generic MCP runtime — never by an LLM at request time.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.schema_model import RelationshipDoc


class TableBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(
        description="'fact' for the primary table, or a related table's own name for a cross-table bind."
    )
    physical: str = Field(description="Real table/view name in the customer's DataSource.")
    grain: str = ""
    join_on: str | None = Field(
        default=None,
        description="For a non-primary table: the ON expression that joins it to the primary "
        "(a verified relationship). None for the primary table itself.",
    )


class ColumnBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(description="Canonical role from the industry pack, e.g. 'revenue_amount'.")
    table_alias: str
    physical: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""
    source: str = Field(
        default="deterministic",
        description="'deterministic' | 'llm_proposed' | 'agent_proposed' | 'human_override'.",
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
    allowed_tables: list[str] = Field(
        description="Every ingested table is queryable - KPIs bind against the primary table, "
        "but the client can read any of them via run_safe_query / search_records."
    )
    relationships: list[RelationshipDoc] = Field(
        default_factory=list,
        description="Verified joins between allowed tables. Empty is normal - the tables may be unrelated.",
    )
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
