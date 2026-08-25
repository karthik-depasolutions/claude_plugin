"""P2-08 — KPI-authoring agent, rewritten to PHASE_2.md's hard contract:
output is `MetricDefinition` with `aggregation: AggOp` and structured
`FilterSpec`, never a free-text SQL/expression field. Supersedes
`kpi_proposer.py`'s `CanonicalKpi`/`sql: "SELECT ..."` design, which is
exactly the `boringdata/boring-semantic-layer` hole PHASE_2.md's "hard
rule" (S0.3) closes by omission.

Single-shot generator, not a tool-using agent (same rationale as the old
kpi_proposer: proposal doesn't need iterative reasoning to be safe, because
safety comes entirely from what happens after this module). What makes this
version safe *by construction*, not just by downstream validation: the
model is never allowed to author a measure, a join, or a filter value
directly. It can only:

  1. Pick an existing `base_metric_id` from the real, already-verified
     catalog `metric_generator.generate_metrics` produced this run - so
     every measure_column/measure_table/measure_join_path/aggregation it
     "proposes" is actually copied verbatim from a fact the deterministic
     P2-01/P2-07 pipeline already established, never agent-authored.
  2. Optionally attach ONE filter naming a value-set already resolved by
     the (deterministic-or-agent-verified) binding layer - `bindings.
     value_sets` - substituted with the customer's REAL observed category
     strings, never a literal the model invented.

The only genuinely agent-authored content is the id/label/description (a
business framing) and which existing metric+filter combination is worth
surfacing as a named question. This is deliberately a much smaller
contribution than kpi_proposer.py's freeform SQL - and that reduction is
the point: P2-08's hard constraint #1 requires "no expression fields
anywhere", and a proposer that can only recombine already-verified facts
satisfies that by construction rather than by a validator catching it
after the fact.

Every accepted candidate still gets a real compile attempt through
`compiler.metric_compiler.render_metric_query` (the same sqlglot-validated
renderer every generated metric goes through) before being returned - a
belt-and-braces check, not the primary safety mechanism.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forge_core.compiler.metric_compiler import MetricCompileError, render_metric_query
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.bindings import SchemaBindings
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.metrics import FilterOp, FilterSpec, MetricDefinition, Provenance
from forge_core.models.quality import render_data_context

MAX_PROPOSED_METRICS = 5

_PROMPT_TEMPLATE = """You are proposing NEW named business questions for a {pack_name} analytics \
plugin, specific to this customer's own data. You are NOT writing SQL and NOT inventing new \
measures or filter values - you may only recombine facts this system has already verified against \
the real data.

EXISTING VERIFIED METRICS (pick from these only - "base_metric_id" must be one of these exactly):
{base_metrics}

RESOLVED VALUE SETS available to filter by (each name maps to the customer's REAL observed \
category values - use the name, never invent a literal):
{value_sets}
{context}
Propose up to {max_metrics} new, business-meaningful named views by picking a base_metric_id and, \
optionally, ONE value-set filter that narrows it to a more specific, useful question (e.g. \
"average score, completed enrollments only" instead of "average score, all enrollments"). Skip \
a filter entirely (omit filter_value_set) when the unfiltered metric is already the interesting \
question - do not force a filter that doesn't add anything.

Return strict JSON: {{"proposals": [{{"id": "snake_case_id", "label": "Human Label", \
"description": "one sentence, business language", "base_metric_id": "...", \
"filter_value_set": "value_set_name or null", "confidence": 0.0-1.0, \
"reasoning": "why this is a useful question for this business"}}]}}

Rules:
- `id` must be lowercase snake_case, unique, and not collide with an existing metric id.
- `base_metric_id` must be copied exactly from the list above - any other value is rejected.
- `filter_value_set`, if given, must be copied exactly from the resolved value sets above.
- `confidence` reflects how confident you are this is a genuinely useful, correctly-framed \
question for THIS business - not how confident you are it will compile."""


def _base_metrics_block(base_metrics: list[MetricDefinition]) -> str:
    lines = [
        f"- {m.id}: {m.label} - {m.description} (unit={m.unit}, filterable_columns="
        f"{sorted({m.measure_column} | {d.physical for d in m.allowed_dimensions})})"
        for m in base_metrics
    ]
    return "\n".join(lines) or "(none generated)"


def _value_sets_block(bindings: SchemaBindings) -> str:
    return "\n".join(f"- {vs.name}: {vs.values}" for vs in bindings.value_sets) or "(none resolved)"


def _filter_for(
    value_set_name: str | None, bindings: SchemaBindings, base: MetricDefinition
) -> FilterSpec | None:
    """Resolves an agent-named value set into a real, structured filter on a
    real filterable column of `base` - or None if the name/column pairing
    doesn't actually exist, in which case the whole candidate is dropped
    rather than shipped unfiltered against the agent's intent."""
    if not value_set_name:
        return None
    vs = bindings.value_set(value_set_name)
    if vs is None or not vs.values:
        return None
    filterable = {d.physical for d in base.allowed_dimensions}
    # A value set is only meaningful against a dimension column already
    # offered on this metric (e.g. "status") - never the measure column
    # itself, which holds numbers, not categories.
    candidate_columns = [c for c in filterable if c.lower() in {"status", "state"} or c in filterable]
    # Prefer a column whose bound role name suggests a status/category field;
    # fall back to any dimension column that exists, since a wrong-but-real
    # column still compiles and the harness downstream would catch nonsense.
    status_like = next((c for c in filterable if any(k in c.lower() for k in ("status", "state"))), None)
    column = status_like or (next(iter(filterable), None))
    if column is None:
        return None
    return FilterSpec(column=column, op=FilterOp.IN, values=list(vs.values))


def propose_metrics(
    pack: IndustryPack,
    bindings: SchemaBindings,
    base_metrics: list[MetricDefinition],
    physical_ref: dict[str, str],
    provider: LLMProvider,
    data_context: dict | None = None,
) -> list[MetricDefinition]:
    """Returns up to `MAX_PROPOSED_METRICS` verified, compile-checked
    `MetricDefinition`s with `source="agent_proposed"`. Never raises -
    degrades to `[]` on any LLM/parsing/compile failure, matching every
    other optional AI-assisted stage in this pipeline."""
    if not base_metrics:
        return []
    context_block = render_data_context(data_context)
    prompt = _PROMPT_TEMPLATE.format(
        pack_name=pack.name,
        base_metrics=_base_metrics_block(base_metrics),
        value_sets=_value_sets_block(bindings),
        context=f"\nCONTEXT FROM THE BUSINESS OWNER:\n{context_block}\n" if context_block else "",
        max_metrics=MAX_PROPOSED_METRICS,
    )
    try:
        raw: Any = provider.generate_json(prompt)
    except LLMError:
        return []
    if not isinstance(raw, dict):
        return []

    base_by_id = {m.id: m for m in base_metrics}
    existing_ids = {m.id for m in base_metrics}
    now = datetime.now(timezone.utc).isoformat()
    results: list[MetricDefinition] = []

    for item in raw.get("proposals", [])[:MAX_PROPOSED_METRICS]:
        if not isinstance(item, dict):
            continue
        new_id = item.get("id")
        base_id = item.get("base_metric_id")
        if not isinstance(new_id, str) or not new_id or new_id in existing_ids:
            continue
        base = base_by_id.get(base_id)
        if base is None:  # the one hard boundary: must be a real, already-verified metric
            continue
        filt = _filter_for(item.get("filter_value_set"), bindings, base)
        if item.get("filter_value_set") and filt is None:
            continue  # named a filter it couldn't actually resolve - drop rather than ship unfiltered

        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5

        candidate = base.model_copy(
            update={
                "id": new_id,
                "label": str(item.get("label") or new_id.replace("_", " ").title()),
                "description": str(item.get("description") or base.description),
                "default_filters": [filt] if filt else [],
                "source": "agent_proposed",
                "prov": Provenance(
                    origin="inferred_llm",
                    confidence=confidence,
                    evidence=[f"derived from verified base metric {base_id!r}"]
                    + ([f"filtered by resolved value set {item.get('filter_value_set')!r}"] if filt else [])
                    + ([str(item["reasoning"])] if isinstance(item.get("reasoning"), str) else []),
                    computed_by="metric_proposer.propose_metrics",
                    computed_at=now,
                    inputs=[base_id],
                ),
            }
        )
        try:
            render_metric_query(candidate, physical_ref)  # compile-check only, result discarded
        except MetricCompileError:
            continue

        results.append(candidate)
        existing_ids.add(new_id)

    return results


__all__ = ["MAX_PROPOSED_METRICS", "propose_metrics"]
