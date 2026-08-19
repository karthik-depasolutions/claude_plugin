"""Compiled KPI contract — output of `forge_core.compiler.kpi_compiler` and
the only KPI representation the generic MCP runtime ever executes.

A CompiledKpi carries fully concrete, sqlglot-parsed SQL. No Jinja
placeholders, no canonical role names — those were resolved by the compiler
using a specific customer's SchemaBindings. This is what gets written to
`config/kpi_defs.json` inside the generated plugin.
"""

from __future__ import annotations

from typing import Literal

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
    source: Literal["pack", "agent_proposed"] = Field(
        default="pack",
        description="'pack' = hand-authored in the industry pack. 'agent_proposed' = proposed by "
        "compiler.kpi_proposer for this customer's data specifically, then compiled/validated "
        "through the exact same gate as every pack KPI - never a different trust boundary.",
    )


class KpiDefsFile(BaseModel):
    """Root object serialized to config/kpi_defs.json."""

    model_config = ConfigDict(extra="forbid")

    pack_slug: str
    generated_at: str
    kpis: list[CompiledKpi]
    skipped: dict[str, str] = Field(
        default_factory=dict,
        description="KPI id -> reason it could not be compiled (unresolved roles, invalid SQL, etc).",
    )

    def get(self, kpi_id: str) -> CompiledKpi | None:
        for k in self.kpis:
            if k.id == kpi_id:
                return k
        return None
