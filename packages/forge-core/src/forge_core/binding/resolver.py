"""Stage 4a — BIND. Produces the SchemaBindings artifact described in the
plan's architectural correction: this is what lets one canonical KPI
definition run against any customer's real column names.

Resolution order per canonical role:
  0. P2-05 data-understanding agent (agentic/data_understanding_agent.py) —
     `use_agent=True` only. One batched session covering every role the
     deterministic scorer alone can't confidently resolve, grounded in the
     precomputed data map, every claim gate-verified (P2-06) before it's
     trusted. This is Phase 2's answer to the exact score->revenue_amount
     bug: the deterministic tier below still runs identically for
     `use_agent=False`/no-provider runs (nothing here changes that path).
  1. Human override (if provided) — always wins.
  2. Deterministic scorer (binding/scorer.py) — if confidence is high enough.
  3. LLM proposer (binding/llm_proposer.py) — only for the remaining gaps,
     and only ever a *proposal*; it must still name a column that exists in
     the fact table, or it is rejected outright, never trusted blindly.
  4. Legacy single-role binding agent (agentic/binding_agent.py) — the
     original P1 tool-using fallback, still tried for anything tier 0/2/3
     left unresolved.
  5. Otherwise the role is left unresolved and every KPI that requires it
     is skipped by the compiler, not silently miscompiled.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Callable

from forge_core.binding.scorer import MIN_BIND_CONFIDENCE, best_candidate, top_candidates
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.bindings import ColumnBinding, SchemaBindings, TableBinding, ValueSetBinding
from forge_core.models.claims import ColumnClaim
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile, temporal_sql_expression
from forge_core.packaging.denial import compute_denied_columns
from forge_core.runtime_session import open_session

_VALUE_SET_PATTERN = re.compile(r"\{\{(\w+)\}\}\s+(?:NOT\s+)?IN\s+\{\{(\w+)\}\}", re.IGNORECASE)
MAX_DISTINCT_VALUES_SCANNED = 50

MIN_CONFIDENCE_RESOLVED = 0.70
"""A tier's result below this does not end the fallthrough for a role - the
next tier still gets a chance (this is what routes the binding agent at
exactly the decisions it was built for: a 0.45 deterministic score no longer
short-circuits before the agent can look at real values). If no tier clears
the bar, the best candidate found still ships - never a hard failure by
itself - but flagged `needs_confirmation=True` so binding/gate.py can turn it
into a question, and only when a shipped KPI actually depends on it."""

_VALUE_SET_PROMPT = """A customer's data has a column with these real, observed distinct values: \
{values}

Decide which of those values genuinely belong to the business concept "{value_set_name}".{hints_block}

Return ONLY JSON: {{"matched": ["<value copied exactly from the list above>", ...]}}. Only include a \
value if it genuinely, semantically belongs — never include one just because it superficially \
resembles a hint below. If you're unsure about a value, leave it out rather than guess."""


def _judge_value_set(
    value_set_name: str, distinct_values: list[str], hints: list[str], provider: LLMProvider | None
) -> tuple[list[str], str]:
    """Which of a column's real distinct values genuinely belong to a
    pack-defined logical value set (e.g. "completed_values"). Judged by an
    LLM against real observed values and the concept's meaning - not
    substring-matched against a pack author's hint list, which is exactly
    the mechanism that shipped 'active' as a member of 'completed_values'
    for one pack (the hint list itself named "active" as a hint - a
    fuzzier matcher wouldn't have caught it either; the fix is judging
    meaning, not hint overlap).

    Falls back to the old substring-hint match only when no LLM is
    configured (--no-llm) - informs, never blocks, same as every other
    optional-LLM stage in this pipeline; `source` on the result says which
    path produced it."""

    def _hint_fallback() -> list[str]:
        return [v for v in distinct_values if any(h in v.lower() for h in hints)]

    if provider is None:
        return _hint_fallback(), "deterministic"

    hints_block = (
        f"\n\nHints from the pack author (may be wrong - verify against real meaning, "
        f"never trust blindly): {hints}"
        if hints
        else ""
    )
    prompt = _VALUE_SET_PROMPT.format(values=distinct_values, value_set_name=value_set_name, hints_block=hints_block)
    try:
        raw = provider.generate_json(prompt)
    except LLMError:
        return _hint_fallback(), "deterministic"
    if not isinstance(raw, dict):
        return _hint_fallback(), "deterministic"

    valid = set(distinct_values)
    matched = [v for v in raw.get("matched", []) if isinstance(v, str) and v in valid]
    return matched, "llm_judged"


_VALUE_SETS_PROMPT = """A customer's data has these business concepts to resolve, each against the \
real, observed distinct values of a column.

{blocks}

For each concept, decide which of ITS listed values genuinely belong to it.

Return ONLY JSON: {{"<concept name>": ["<value copied exactly from that concept's list>", ...], ...}}

Only include a value if it genuinely, semantically belongs - never because it superficially resembles \
a hint. If you're unsure about a value, leave it out rather than guess. A concept with no matching \
value gets an empty list. Concepts drawn from the same column are usually mutually exclusive: a value \
that means "completed" is not also "cancelled"."""


def _judge_value_sets(
    jobs: list[tuple[str, list[str], list[str]]], provider: LLMProvider | None
) -> list[ValueSetBinding]:
    """Judge every value set in one call. Same contract as the single-set
    version below, which remains the per-set fallback."""

    def _fallback() -> list[ValueSetBinding]:
        return [
            ValueSetBinding(
                name=name,
                values=[v for v in values if any(h in v.lower() for h in hints)],
                source="deterministic",
            )
            for name, values, hints in jobs
        ]

    if provider is None:
        return _fallback()

    blocks = "\n\n".join(
        f'Concept "{name}"\n  observed values: {values}'
        + (f"\n  hints from the pack author (may be wrong - verify against real meaning): {hints}" if hints else "")
        for name, values, hints in jobs
    )
    try:
        raw = provider.generate_json(_VALUE_SETS_PROMPT.format(blocks=blocks))
    except LLMError:
        return _fallback()
    if not isinstance(raw, dict):
        return _fallback()

    results: list[ValueSetBinding] = []
    for name, values, hints in jobs:
        valid = set(values)
        matched = raw.get(name)
        if not isinstance(matched, list):
            # One malformed entry falls back for that concept alone, not all.
            results.append(
                ValueSetBinding(
                    name=name,
                    values=[v for v in values if any(h in v.lower() for h in hints)],
                    source="deterministic",
                )
            )
            continue
        results.append(
            ValueSetBinding(
                name=name,
                values=[v for v in matched if isinstance(v, str) and v in valid],
                source="llm_judged",
            )
        )
    return results


def _is_denied(column: ColumnProfile, pack: IndustryPack) -> bool:
    return (
        column.is_likely_pii
        or column.guessed_role.value in pack.guardrails.denied_role_categories
    )


def pick_fact_table(profile: SchemaProfile, pack: IndustryPack) -> str:
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


def _notes_block(notes: list[dict]) -> str:
    """The data-review answers, rendered as a short block appended to every
    role's description so both the single-shot proposer and the agent see
    what the business owner told us before they decide a binding. Empty
    notes render to "" so a no-context run stays byte-identical to before."""
    if not notes:
        return ""
    lines = "\n".join(f'- "{n["question"]}" -> "{n["answer"]}"' for n in notes if n.get("answer"))
    return f"\n\nContext the business owner gave about this data:\n{lines}"


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
    notes: list[dict] | None = None,
    tenant_id: str = "_local",
    on_agent_stats: Callable[[dict], None] | None = None,
    agent_claims: dict[str, tuple[str, ColumnClaim]] | None = None,
    batched_agent_ran: bool = False,
) -> tuple[list[ColumnBinding], list[str]]:
    """`batched_agent_ran` says the all-roles agent pass actually executed.

    It gates the per-role agent below (tier 4). That tier predates the
    batched pass and asks the *same agent* the *same question* with strictly
    less context - the batched pass sees the data map, real values, and every
    role at once, then gate-verifies each claim. Re-asking per role once it
    has already declined bought nothing and dominated the run's cost: 42k
    tokens across ~20 extra LLM calls on a 6-role pack, more than the whole
    rest of the pipeline combined.

    When the batched pass declined a role, the honest outcome is
    needs-confirmation, not three more guesses at it."""
    bindings: list[ColumnBinding] = []
    unresolved: list[str] = []
    valid_names = {c.name for c in table_cols}
    notes_block = _notes_block(notes or [])
    agent_claims = agent_claims or {}

    for role, description in pack.canonical_roles.items():
        if role in overrides:
            # The documented override form is "table.column" (see
            # schemas.py's BindingOverridesRequest) - but the fact table is
            # always the one being bound, so the table qualifier is dropped
            # and only the column name is matched against the fact table's
            # real columns. Previously only the bare name ever matched, so
            # the documented "table.column" form silently did nothing.
            wanted = overrides[role].split(".")[-1]
            if wanted in valid_names:
                bindings.append(
                    ColumnBinding(
                        role=role,
                        table_alias="fact",
                        physical=wanted,
                        confidence=1.0,
                        evidence="human override",
                        source="human_override",
                    )
                )
                continue

        if role in agent_claims:
            physical, claim = agent_claims[role]
            candidate = ColumnBinding(
                role=role,
                table_alias="fact",
                physical=physical,
                confidence=claim.confidence,
                evidence=f"agent-verified ({claim.kind}, unit={claim.unit}): {claim.meaning}",
                source="agent_proposed",
            )
            if candidate.confidence >= MIN_CONFIDENCE_RESOLVED:
                bindings.append(candidate)
                continue
            # Below threshold even after gate-verification - still the best
            # signal available for this role; falls into the same
            # needs-confirmation path as every other tier below, never
            # trusted silently just because it passed the gates.
            best_so_far_from_agent = candidate
        else:
            best_so_far_from_agent = None

        hinted_description = f"{description}{notes_block}"
        hints = tuple(pack.role_hints.get(role, ()))

        # Ranked once, up front, deterministically - both the tier-1 signal
        # and the pool a needs-confirmation binding's `alternatives` is drawn
        # from, regardless of which tier ends up supplying the winner.
        ranked = top_candidates(role, table_cols, hints, n=4)
        alt_pool = [(c.column.name, c.confidence) for c in ranked]
        best_so_far: ColumnBinding | None = best_so_far_from_agent

        if ranked and ranked[0].confidence >= MIN_BIND_CONFIDENCE:
            top = ranked[0]
            candidate = ColumnBinding(
                role=role,
                table_alias="fact",
                physical=top.column.name,
                confidence=top.confidence,
                evidence=top.evidence,
                source="deterministic",
            )
            if candidate.confidence >= MIN_CONFIDENCE_RESOLVED:
                bindings.append(candidate)
                continue
            if best_so_far is None or candidate.confidence > best_so_far.confidence:
                best_so_far = candidate

        if provider is not None:
            from forge_core.binding.llm_proposer import propose_binding

            proposed = propose_binding(role, hinted_description, table_cols, provider)
            if proposed and proposed in valid_names:
                candidate = ColumnBinding(
                    role=role,
                    table_alias="fact",
                    physical=proposed,
                    confidence=0.55,
                    evidence="LLM-proposed; below deterministic confidence threshold",
                    source="llm_proposed",
                )
                if candidate.confidence >= MIN_CONFIDENCE_RESOLVED:
                    bindings.append(candidate)
                    continue
                if best_so_far is None or candidate.confidence > best_so_far.confidence:
                    best_so_far = candidate

        if (
            use_agent
            and not batched_agent_ran
            and source is not None
            and fact_table_physical_ref is not None
        ):
            from forge_core.agentic import propose_binding_with_agent

            agent_proposed = propose_binding_with_agent(
                role,
                hinted_description,
                table_cols,
                source,
                fact_table_physical_ref,
                pack_slug=pack.slug,
                tenant_id=tenant_id,
                # User context must invalidate a cached decision (memory.py's
                # schema_fingerprint folds extra into the signature), so the
                # agent never reuses a decision made without these notes.
                context_extra=json.dumps(notes or [], sort_keys=True),
                on_stats=on_agent_stats,
            )
            if agent_proposed and agent_proposed in valid_names:
                candidate = ColumnBinding(
                    role=role,
                    table_alias="fact",
                    physical=agent_proposed,
                    confidence=0.6,
                    evidence="agent-proposed; reasoned over real sample data before deciding",
                    source="agent_proposed",
                )
                if candidate.confidence >= MIN_CONFIDENCE_RESOLVED:
                    bindings.append(candidate)
                    continue
                if best_so_far is None or candidate.confidence > best_so_far.confidence:
                    best_so_far = candidate

        if best_so_far is not None:
            others = [(name, conf) for name, conf in alt_pool if name != best_so_far.physical][:3]
            bindings.append(
                best_so_far.model_copy(update={"needs_confirmation": True, "alternatives": others})
            )
        else:
            unresolved.append(role)

    return _attach_temporal_expressions(bindings, table_cols), unresolved


def _attach_temporal_expressions(
    bindings: list[ColumnBinding], table_cols: list[ColumnProfile]
) -> list[ColumnBinding]:
    """Give every binding on a non-ISO date column the STRPTIME expression
    its KPIs need.

    Applied once here rather than at each of the five tiers that construct a
    ColumnBinding: a tier added later would otherwise silently omit it, and
    the failure mode is severe and remote from the cause - DuckDB's
    `CAST('02-05-1993' AS TIMESTAMP)` raises instead of returning NULL, so a
    single such column takes the whole build down at dry-run with a SQL error
    rather than degrading to a skipped KPI."""
    fmt_by_column = {
        c.name: c.temporal_format for c in table_cols if c.temporal_format
    }
    if not fmt_by_column:
        return bindings
    return [
        b.model_copy(
            update={
                "sql_expression": temporal_sql_expression(b.physical, fmt_by_column[b.physical])
            }
        )
        if b.physical in fmt_by_column and b.sql_expression is None
        else b
        for b in bindings
    ]


def _resolve_value_sets(
    pack: IndustryPack,
    fact_physical_ref: str,
    columns: list[ColumnBinding],
    profile: SchemaProfile,
    provider: LLMProvider | None = None,
) -> list[ValueSetBinding]:
    pairs: set[tuple[str, str]] = set()
    for kpi in pack.kpis:
        pairs.update(_VALUE_SET_PATTERN.findall(kpi.sql))

    if not pairs:
        return []

    con = open_session(profile.source)
    try:
        # Gather every value set first, then judge them in ONE call.
        # A pack's sets are usually drawn from the same column
        # ("completed_values" and "cancelled_values" both partition
        # transaction_status), so judging them separately re-sent an
        # identical value list per set - three sequential round trips where
        # one does strictly better, since deciding them together is also what
        # makes them mutually consistent.
        distinct_cache: dict[str, list[str]] = {}
        jobs: list[tuple[str, list[str], list[str]]] = []
        for role, value_set_name in sorted(pairs):
            binding = next((c for c in columns if c.role == role), None)
            if binding is None:
                continue
            if binding.physical not in distinct_cache:
                rows = con.execute(
                    f'SELECT DISTINCT "{binding.physical}" FROM {fact_physical_ref} '
                    f'WHERE "{binding.physical}" IS NOT NULL LIMIT {MAX_DISTINCT_VALUES_SCANNED}'
                ).fetchall()
                distinct_cache[binding.physical] = [str(r[0]) for r in rows]
            distinct_values = distinct_cache[binding.physical]
            if not distinct_values:
                continue
            jobs.append((value_set_name, distinct_values, pack.value_set_hints.get(value_set_name, [])))

        if not jobs:
            return []
        return _judge_value_sets(jobs, provider)
    finally:
        con.close()


def _propose_agent_claims_for_weak_roles(
    profile: SchemaProfile,
    pack: IndustryPack,
    bindable_cols: list[ColumnProfile],
    overrides: dict[str, str],
    denied_columns: set[str],
    *,
    tenant_id: str,
    on_agent_stats: Callable[[dict], None] | None,
) -> dict[str, tuple[str, ColumnClaim]]:
    """The agent is asked about EVERY unoverridden role, not just the ones
    the deterministic scorer scored weakly - a confidently-WRONG name match
    (e.g. `score` vs `revenue_amount` scoring high on token overlap alone)
    used to keep the agent from ever seeing that role at all, which is
    exactly backwards: those are the cases that most need real value
    evidence, not fewer. The scorer's ranking still matters - it's threaded
    into the agent's prompt as evidence to verify or refute, not as a
    pre-filter deciding what the agent is allowed to look at. Roles already
    satisfied by a human override are excluded; an override always wins
    regardless of what the agent might say."""
    roles_to_ask: dict[str, str] = {
        role: description for role, description in pack.canonical_roles.items() if role not in overrides
    }
    if not roles_to_ask:
        return {}

    from forge_core.agentic.data_understanding_agent import propose_bindings_with_agent

    return propose_bindings_with_agent(
        roles_to_ask,
        bindable_cols,
        profile.structural.data_map,
        profile.source,
        profile.structural,
        denied_columns,
        pack_slug=pack.slug,
        tenant_id=tenant_id,
        on_stats=on_agent_stats,
    )


def resolve_bindings(
    profile: SchemaProfile,
    pack: IndustryPack,
    provider: LLMProvider | None = None,
    overrides: dict[str, str] | None = None,
    *,
    use_agent: bool = False,
    data_context: dict | None = None,
    tenant_id: str = "_local",
    on_agent_stats: Callable[[dict], None] | None = None,
    on_agent_claims: Callable[[dict[str, tuple[str, ColumnClaim]]], None] | None = None,
) -> SchemaBindings:
    overrides = overrides or {}
    fact_table_name = pick_fact_table(profile, pack)
    fact_table = profile.source.table(fact_table_name)
    table_cols = [c for c in profile.structural.columns if c.table == fact_table_name]
    bindable_cols = [c for c in table_cols if not _is_denied(c, pack)]
    notes = (data_context or {}).get("notes") or []

    # P2-01: every dimension table the entity graph can verify a real join
    # path to becomes reachable too - not just the fact table - so
    # run_safe_query stops being a dead end at the second table (review
    # P1.1). Canonical-role binding itself stays fact-table-scoped by
    # design (ADR 0001: every pack KPI only ever references {{fact}}).
    reachable_tables = {fact_table_name}
    entity_graph = profile.structural.entity_graph
    if entity_graph is not None:
        reachable_tables |= entity_graph.reachable_tables(fact_table_name)

    # Denied columns must cover every reachable table, not just the fact
    # table - check_no_denied_columns matches by column name across the
    # whole query, so a PII column on a newly-reachable dimension table
    # (e.g. students.full_name) needs to be in this list too, or widening
    # allowed_tables would silently widen what run_safe_query can leak.
    denied_by_table = compute_denied_columns(profile, pack)
    denied_columns = sorted(
        {name for table in reachable_tables for name in denied_by_table.get(table, ())}
    )

    agent_claims: dict[str, tuple[str, ColumnClaim]] = {}
    batched_agent_ran = False
    # An agent IS an LLM call - use_agent=True with no provider (use_llm=
    # False upstream) must not silently make one anyway, matching every
    # other agent gate in orchestrator.py (`use_agent and profiling_
    # provider is not None`). Without this, a caller's explicit "no LLM"
    # was silently overridden the moment use_agent's own default became
    # True for every real run.
    if use_agent and provider is not None:
        agent_claims = _propose_agent_claims_for_weak_roles(
            profile, pack, bindable_cols, overrides, set(denied_columns),
            tenant_id=tenant_id, on_agent_stats=on_agent_stats,
        )
        batched_agent_ran = True
        # generate_metrics (P2-07) needs these too, keyed by physical
        # column rather than canonical role - the ONLY wire from a verified
        # semantic claim to what actually gets built (see orchestrator.py's
        # own note on the "dead-end wiring" this closes).
        if on_agent_claims is not None:
            on_agent_claims(agent_claims)

    columns, unresolved = _resolve_columns(
        fact_table_name,
        bindable_cols,
        pack,
        provider=provider,
        overrides=overrides,
        source=profile.source,
        fact_table_physical_ref=fact_table.physical_ref,
        use_agent=use_agent,
        agent_claims=agent_claims,
        batched_agent_ran=batched_agent_ran,
        notes=notes,
        tenant_id=tenant_id,
        on_agent_stats=on_agent_stats,
    )

    value_sets = _resolve_value_sets(pack, fact_table.physical_ref, columns, profile, provider)

    dimension_tables = sorted(reachable_tables - {fact_table_name})
    tables = [TableBinding(alias="fact", physical=fact_table.physical_ref, grain=fact_table_name)]
    for name in dimension_tables:
        physical = profile.source.table(name).physical_ref
        tables.append(TableBinding(alias=name, physical=physical, grain=name))

    return SchemaBindings(
        pack_slug=pack.slug,
        data_source_id=profile.data_source_id,
        tables=tables,
        columns=columns,
        value_sets=value_sets,
        allowed_tables=[t.physical for t in tables],
        denied_columns=denied_columns,
        unresolved_roles=unresolved,
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
