"""Compiled KPI contract — output of `forge_core.compiler.kpi_compiler` and
the only KPI representation the generic MCP runtime ever executes.

A CompiledKpi carries fully concrete, sqlglot-parsed SQL. No Jinja
placeholders, no canonical role names — those were resolved by the compiler
using a specific customer's SchemaBindings. This is what gets written to
`config/kpi_defs.json` inside the generated plugin.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CompiledKpi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    unit: str
    sql: str = Field(description="Concrete, dialect-checked SELECT statement. No templating left.")
    assertions: list[str] = Field(default_factory=list)
    result_columns: list[str] = Field(default_factory=list)
    source_kpi_id: str = Field(description="The CanonicalKpi.id this was compiled from.")


class KpiDefsFile(BaseModel):
    """Root object serialized to config/kpi_defs.json."""

    model_config = ConfigDict(extra="forbid")

    pack_slug: str
    generated_at: str
    kpis: list[CompiledKpi]
    skipped: list[str] = Field(
        default_factory=list,
        description="KPI ids that could not be compiled because required roles were unresolved.",
    )

    def get(self, kpi_id: str) -> CompiledKpi | None:
        for k in self.kpis:
            if k.id == kpi_id:
                return k
        return None
