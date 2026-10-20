"""Stage 4b — COMPILE_KPIS. Joins an IndustryPack's canonical KPIs with a
customer's SchemaBindings into concrete, sqlglot-validated SQL. This is the
only KPI representation the generic MCP runtime ever executes — see
docs/architecture.md §2 for why that boundary matters.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlglot
from sqlglot.expressions import Expression, Select
from sqlglot.expressions import With as WithExpr

from forge_core.compiler.sql_render import UnresolvedTokenError, render_sql
from forge_core.models.bindings import SchemaBindings
from forge_core.models.industry_pack import CanonicalKpi, IndustryPack
from forge_core.models.kpi import CompiledKpi, KpiDefsFile

ALLOWED_ROOT_EXPRESSIONS = (Select, WithExpr)


class KpiCompileError(ValueError):
    pass


def _outermost_select(parsed: Expression) -> Select | None:
    """The SELECT that produces the final result set: `parsed` itself for a
    plain SELECT, or the query body of a `WITH ... SELECT` (never a CTE's own
    SELECT). That is where a cross-table JOIN must be attached."""
    if isinstance(parsed, Select):
        return parsed
    if isinstance(parsed, WithExpr):
        body = parsed.this
        return body if isinstance(body, Select) else None
    return None


def _all_required_roles(kpi: CanonicalKpi) -> list[str]:
    r = kpi.requires
    return [*r.measures, *r.dimensions, *r.filters, *r.entities]


def _roles_available(kpi: CanonicalKpi, bindings: SchemaBindings) -> bool:
    resolved_roles = {c.role for c in bindings.columns}
    return all(role in resolved_roles for role in _all_required_roles(kpi))


def compile_kpi(kpi: CanonicalKpi, bindings: SchemaBindings) -> CompiledKpi:
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

    # Cross-table roles: LEFT JOIN only the related tables THIS KPI actually
    # references (via a required role), plus any intermediate table on the join
    # path to one of those (a multi-hop chain) - never every cross-table bind.
    cross_by_alias = {t.alias: t for t in bindings.tables if t.join_on}
    needed = {
        cb.table_alias
        for role in _all_required_roles(kpi)
        if (cb := bindings.column(role)) is not None and cb.table_alias in cross_by_alias
    }
    # transitively pull in any cross-table whose physical name a needed join
    # references (the intermediate hops of a chain).
    changed = True
    while changed:
        changed = False
        for alias, tb in cross_by_alias.items():
            if alias in needed:
                continue
            # tb is an intermediate hop if a table we already need joins through it.
            if any(tb.physical in (cross_by_alias[a].join_on or "") for a in needed):
                needed.add(alias)
                changed = True
    joins = [t for t in bindings.tables if t.alias in needed]
    if joins:
        # Refuse a one-to-many (child) join: a blind LEFT JOIN multiplies the
        # primary's rows and corrupts SUM/COUNT. Declarative pre-aggregation is
        # the path for those (see the join-graph work); until then, be honest.
        fanning = [t.alias for t in joins if not t.join_is_to_parent]
        if fanning:
            raise KpiCompileError(
                f"KPI {kpi.id!r} needs a one-to-many join to {fanning} which would inflate its "
                "aggregates; not supported yet"
            )
        target = _outermost_select(parsed)
        if target is None:
            raise KpiCompileError(f"KPI {kpi.id!r}: could not find a SELECT to attach the join to")
        for tb in joins:
            target.join(tb.physical, on=tb.join_on, join_type="LEFT", dialect="duckdb", copy=False)

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
    )


def compile_all(pack: IndustryPack, bindings: SchemaBindings) -> KpiDefsFile:
    compiled: list[CompiledKpi] = []
    skipped: list[str] = []

    for kpi in pack.kpis:
        try:
            compiled.append(compile_kpi(kpi, bindings))
        except KpiCompileError:
            skipped.append(kpi.id)

    return KpiDefsFile(
        pack_slug=pack.slug,
        generated_at=datetime.now(UTC).isoformat(),
        kpis=compiled,
        skipped=skipped,
    )
