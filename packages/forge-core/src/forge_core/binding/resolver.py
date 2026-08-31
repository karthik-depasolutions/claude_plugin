"""Stage 4a — BIND. Produces the SchemaBindings artifact described in the
plan's architectural correction: this is what lets one canonical KPI
definition run against any customer's real column names.

Resolution order per canonical role:
  1. Human override (if provided) — always wins.
  2. Deterministic scorer (binding/scorer.py) — if confidence is high enough.
  3. LLM proposer (binding/llm_proposer.py) — only for the remaining gaps,
     and only ever a *proposal*; it must still name a column that exists in
     the fact table, or it is rejected outright, never trusted blindly.
  4. Otherwise the role is left unresolved and every KPI that requires it
     is skipped by the compiler, not silently miscompiled.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from forge_core.binding.scorer import MIN_BIND_CONFIDENCE, best_candidate
from forge_core.llm.provider import LLMProvider
from forge_core.models.bindings import ColumnBinding, SchemaBindings, TableBinding, ValueSetBinding
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_model import RelationshipDoc
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile
from forge_core.runtime_session import open_session

_VALUE_SET_PATTERN = re.compile(r"\{\{(\w+)\}\}\s+(?:NOT\s+)?IN\s+\{\{(\w+)\}\}", re.IGNORECASE)
MAX_DISTINCT_VALUES_SCANNED = 50


def _is_denied(column: ColumnProfile, pack: IndustryPack) -> bool:
    return column.guessed_role.value in pack.guardrails.denied_role_categories


def pick_primary_table(profile: SchemaProfile, pack: IndustryPack) -> str:
    """The one table pack KPIs bind their roles against (the `{{fact}}`
    template token). Every other ingested table is still queryable and a
    role a KPI needs but the primary lacks can bind to a directly-related
    table - see `_resolve_cross_table`."""
    tables = profile.source.tables
    if len(tables) == 1:
        return tables[0].name

    if profile.semantic and profile.semantic.likely_central_entities:
        for entity_name in profile.semantic.likely_central_entities:
            if any(t.name == entity_name for t in tables):
                return entity_name

    best_table, best_score = tables[0].name, -1.0
    for table in tables:
        table_cols = [
            c
            for c in profile.structural.columns
            if c.table == table.name and not _is_denied(c, pack)
        ]
        total = 0.0
        for role in pack.canonical_roles:
            hints = tuple(pack.role_hints.get(role, ()))
            candidate = best_candidate(role, table_cols, hints)
            if candidate and candidate.confidence >= MIN_BIND_CONFIDENCE:
                total += candidate.confidence
        if total > best_score:
            best_table, best_score = table.name, total
    return best_table


def _resolve_columns(
    fact_table: str,
    table_cols: list[ColumnProfile],
    pack: IndustryPack,
    *,
    provider: LLMProvider | None,
    overrides: dict[str, str],
    source: DataSource | None = None,
    fact_table_physical_ref: str | None = None,
    use_agent: bool = False,
) -> tuple[list[ColumnBinding], list[str]]:
    bindings: list[ColumnBinding] = []
    unresolved: list[str] = []
    valid_names = {c.name for c in table_cols}

    for role, description in pack.canonical_roles.items():
        if role in overrides and overrides[role] in valid_names:
            bindings.append(
                ColumnBinding(
                    role=role,
                    table_alias="fact",
                    physical=overrides[role],
                    confidence=1.0,
                    evidence="human override",
                    source="human_override",
                )
            )
            continue

        hints = tuple(pack.role_hints.get(role, ()))
        candidate = best_candidate(role, table_cols, hints)
        if candidate and candidate.confidence >= MIN_BIND_CONFIDENCE:
            bindings.append(
                ColumnBinding(
                    role=role,
                    table_alias="fact",
                    physical=candidate.column.name,
                    confidence=candidate.confidence,
                    evidence=candidate.evidence,
                    source="deterministic",
                )
            )
            continue

        if provider is not None:
            from forge_core.binding.llm_proposer import propose_binding

            proposed = propose_binding(role, description, table_cols, provider)
            if proposed and proposed in valid_names:
                bindings.append(
                    ColumnBinding(
                        role=role,
                        table_alias="fact",
                        physical=proposed,
                        confidence=0.55,
                        evidence="LLM-proposed; below deterministic confidence threshold",
                        source="llm_proposed",
                    )
                )
                continue

        if use_agent and source is not None and fact_table_physical_ref is not None:
            from forge_core.agentic import propose_binding_with_agent

            agent_proposed = propose_binding_with_agent(
                role, description, table_cols, source, fact_table_physical_ref, pack_slug=pack.slug
            )
            if agent_proposed and agent_proposed in valid_names:
                bindings.append(
                    ColumnBinding(
                        role=role,
                        table_alias="fact",
                        physical=agent_proposed,
                        confidence=0.6,
                        evidence="agent-proposed; reasoned over real sample data before deciding",
                        source="agent_proposed",
                    )
                )
                continue

        unresolved.append(role)

    return bindings, unresolved


def _resolve_value_sets(
    pack: IndustryPack,
    fact_physical_ref: str,
    columns: list[ColumnBinding],
    profile: SchemaProfile,
) -> list[ValueSetBinding]:
    pairs: set[tuple[str, str]] = set()
    for kpi in pack.kpis:
        pairs.update(_VALUE_SET_PATTERN.findall(kpi.sql))

    if not pairs:
        return []

    con = open_session(profile.source)
    try:
        results: list[ValueSetBinding] = []
        for role, value_set_name in pairs:
            binding = next((c for c in columns if c.role == role), None)
            hints = pack.value_set_hints.get(value_set_name, [])
            if binding is None or not hints:
                continue
            rows = con.execute(
                f'SELECT DISTINCT "{binding.physical}" FROM {fact_physical_ref} '
                f'WHERE "{binding.physical}" IS NOT NULL LIMIT {MAX_DISTINCT_VALUES_SCANNED}'
            ).fetchall()
            distinct_values = [str(r[0]) for r in rows]
            matched = [v for v in distinct_values if any(h in v.lower() for h in hints)]
            results.append(ValueSetBinding(name=value_set_name, values=matched, source="deterministic"))
        return results
    finally:
        con.close()


def resolve_bindings(
    profile: SchemaProfile,
    pack: IndustryPack,
    provider: LLMProvider | None = None,
    overrides: dict[str, str] | None = None,
    *,
    use_agent: bool = False,
) -> SchemaBindings:
    overrides = overrides or {}
    primary_table_name = pick_primary_table(profile, pack)
    primary_table = profile.source.table(primary_table_name)
    # KPI roles bind against the primary table first, but *every* ingested
    # table is exposed for querying, and a role the primary lacks can bind to
    # a directly-related table (see _resolve_cross_table). A denied role
    # category (free_text) is stripped wherever it appears.
    primary_cols = [c for c in profile.structural.columns if c.table == primary_table_name]
    denied_columns = sorted({c.name for c in profile.structural.columns if _is_denied(c, pack)})
    bindable_cols = [c for c in primary_cols if not _is_denied(c, pack)]

    columns, unresolved = _resolve_columns(
        primary_table_name,
        bindable_cols,
        pack,
        provider=provider,
        overrides=overrides,
        source=profile.source,
        fact_table_physical_ref=primary_table.physical_ref,
        use_agent=use_agent,
    )

    table_bindings = [
        TableBinding(alias="fact", physical=primary_table.physical_ref, grain=primary_table_name)
    ]
    # A role can also bind to a table one verified relationship hop away -
    # tried for roles the primary left unresolved, and for roles the primary
    # bound only weakly (a category match with no real name signal), where a
    # clearly-better related column should win.
    _WEAK = 0.55
    primary_conf = {c.role: c.confidence for c in columns if c.source == "deterministic"}
    candidates = [*unresolved, *(r for r, c in primary_conf.items() if c < _WEAK)]
    cross = _resolve_cross_table(candidates, pack, profile, primary_table_name, denied_columns)
    for cb, tb in cross:
        prior = primary_conf.get(cb.role)
        if prior is not None and cb.confidence <= prior + 0.1:
            continue  # the primary's weak bind still stands - not a clear enough improvement
        columns = [c for c in columns if c.role != cb.role]
        columns.append(cb)
        if all(existing.alias != tb.alias for existing in table_bindings):
            table_bindings.append(tb)
    bound_cross = {cb.role for cb, _ in cross}
    still_unresolved = [r for r in unresolved if r not in bound_cross]

    value_sets = _resolve_value_sets(pack, primary_table.physical_ref, columns, profile)

    return SchemaBindings(
        pack_slug=pack.slug,
        data_source_id=profile.data_source_id,
        tables=table_bindings,
        columns=columns,
        value_sets=value_sets,
        allowed_tables=[t.physical_ref for t in profile.source.tables],
        relationships=[
            RelationshipDoc(
                from_ref=f"{r.from_table}.{r.from_column}",
                to_ref=f"{r.to_table}.{r.to_column}",
                strength=r.strength,
            )
            for r in profile.structural.relationships
        ],
        denied_columns=denied_columns,
        unresolved_roles=still_unresolved,
    )


def _resolve_cross_table(
    roles: list[str],
    pack: IndustryPack,
    profile: SchemaProfile,
    primary_table_name: str,
    denied_columns: list[str],
) -> list[tuple[ColumnBinding, TableBinding]]:
    """For each role, try the best column on a table one verified relationship
    hop from the primary. The caller decides whether it beats an existing bind."""
    if not roles:
        return []

    physical = {t.name: t.physical_ref for t in profile.source.tables}
    # table_name -> ON expression joining it to the primary
    joinable: dict[str, str] = {}
    for r in profile.structural.relationships:
        p = physical.get(primary_table_name)
        if r.from_table == primary_table_name and r.to_table in physical:
            joinable[r.to_table] = (
                f'"{p}"."{r.from_column}" = "{physical[r.to_table]}"."{r.to_column}"'
            )
        elif r.to_table == primary_table_name and r.from_table in physical:
            joinable[r.from_table] = (
                f'"{p}"."{r.to_column}" = "{physical[r.from_table]}"."{r.from_column}"'
            )

    denied = set(denied_columns)
    out: list[tuple[ColumnBinding, TableBinding]] = []
    for role in roles:
        hints = tuple(pack.role_hints.get(role, ()))
        best: tuple[float, str, str] | None = None  # (confidence, table_name, column_name)
        for table_name in joinable:
            cols = [
                c
                for c in profile.structural.columns
                if c.table == table_name and c.name not in denied
            ]
            candidate = best_candidate(role, cols, hints)
            if candidate and candidate.confidence >= MIN_BIND_CONFIDENCE and (
                best is None or candidate.confidence > best[0]
            ):
                best = (candidate.confidence, table_name, candidate.column.name)
        if best is None:
            continue
        _, table_name, column_name = best
        out.append(
            (
                ColumnBinding(
                    role=role,
                    table_alias=table_name,
                    physical=column_name,
                    confidence=best[0],
                    evidence=f"resolved on related table {table_name!r} (one relationship hop)",
                    source="cross_table",
                ),
                TableBinding(
                    alias=table_name,
                    physical=physical[table_name],
                    grain=table_name,
                    join_on=joinable[table_name],
                ),
            )
        )
    return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
