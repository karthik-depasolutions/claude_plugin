"""Stage 4b — COMPILE_KPIS. Joins an IndustryPack's canonical KPIs with a
customer's SchemaBindings into concrete, sqlglot-validated SQL. This is the
only KPI representation the generic MCP runtime ever executes — see
docs/architecture.md §2 for why that boundary matters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import sqlglot
from sqlglot.expressions import Select
from sqlglot.expressions import With as WithExpr

from forge_core.compiler.sql_render import UnresolvedTokenError, render_sql
from forge_core.models.bindings import SchemaBindings
from forge_core.models.industry_pack import CanonicalKpi, IndustryPack
from forge_core.models.kpi import CompiledKpi, KpiDefsFile

ALLOWED_ROOT_EXPRESSIONS = (Select, WithExpr)


class KpiCompileError(ValueError):
    pass


def _all_required_roles(kpi: CanonicalKpi) -> list[str]:
    r = kpi.requires
    return [*r.measures, *r.dimensions, *r.filters, *r.entities]


def _roles_available(kpi: CanonicalKpi, bindings: SchemaBindings) -> bool:
    resolved_roles = {c.role for c in bindings.columns}
    return all(role in resolved_roles for role in _all_required_roles(kpi))


def compile_kpi(
    kpi: CanonicalKpi, bindings: SchemaBindings, *, source: Literal["pack", "agent_proposed"] = "pack"
) -> CompiledKpi:
    if not _roles_available(kpi, bindings):
        missing = [r for r in _all_required_roles(kpi) if r not in {c.role for c in bindings.columns}]
        raise KpiCompileError(f"KPI {kpi.id!r} requires unresolved role(s): {missing}")

    try:
        concrete_sql = render_sql(kpi.sql, bindings)
    except UnresolvedTokenError as exc:
        raise KpiCompileError(f"KPI {kpi.id!r}: {exc}") from exc

    try:
        parsed = sqlglot.parse_one(concrete_sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise KpiCompileError(f"KPI {kpi.id!r} produced invalid SQL: {exc}\nSQL: {concrete_sql}") from exc

    if not isinstance(parsed, ALLOWED_ROOT_EXPRESSIONS):
        raise KpiCompileError(f"KPI {kpi.id!r} must compile to a SELECT/WITH statement, got {type(parsed)}")

    result_columns = [
        (col.alias_or_name or "column") for col in parsed.selects
    ] if isinstance(parsed, Select) else []

    return CompiledKpi(
        id=kpi.id,
        label=kpi.label,
        description=kpi.description,
        unit=kpi.unit,
        sql=parsed.sql(dialect="duckdb"),
        assertions=kpi.assertions,
        result_columns=result_columns,
        source_kpi_id=kpi.id,
        source=source,
    )


def compile_all(pack: IndustryPack, bindings: SchemaBindings) -> KpiDefsFile:
    compiled: list[CompiledKpi] = []
    skipped: dict[str, str] = {}

    for kpi in pack.kpis:
        try:
            compiled.append(compile_kpi(kpi, bindings))
        except KpiCompileError as exc:
            skipped[kpi.id] = str(exc)

    return KpiDefsFile(
        pack_slug=pack.slug,
        generated_at=datetime.now(UTC).isoformat(),
        kpis=compiled,
        skipped=skipped,
    )
