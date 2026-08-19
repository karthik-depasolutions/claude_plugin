"""Stage 4c (optional, `use_agent=True`) — proposes a small, capped batch of
additional `CanonicalKpi` candidates specific to this customer's bound data,
on top of whatever the industry pack already defines by hand.

A single-shot generator, not a tool-using agent — unlike the binding or
data-understanding agents, a KPI candidate doesn't need iterative reasoning
to be safe, because its safety comes entirely from what happens *after*
this module: every candidate is compiled through the exact same
`compiler.kpi_compiler.compile_kpi` gate every hand-authored pack KPI
already goes through (Jinja-token substitution against real bindings,
sqlglot parse/validate, SELECT/WITH-only root). A candidate this module
gets wrong just fails to compile and lands in `KpiDefsFile.skipped` with a
reason — never a new trust surface, never raw SQL taken on faith.
"""

from __future__ import annotations

from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.bindings import SchemaBindings
from forge_core.models.industry_pack import CanonicalKpi, IndustryPack
from forge_core.models.quality import render_data_context

MAX_PROPOSED_KPIS = 5

_PROMPT_TEMPLATE = """You are proposing NEW KPIs for a {pack_name} analytics plugin - specific to \
this customer's own data, not generic template metrics. Every KPI you propose must be genuinely \
groundable in the real roles/tables/values listed below; you are not writing SQL directly against \
physical columns, only against the canonical tokens listed here.

KPIs ALREADY DEFINED (do not propose duplicates or near-duplicates of these):
{existing}

AVAILABLE CANONICAL ROLES - reference as {{{{role_name}}}} in your sql, never invent one:
{roles}

AVAILABLE TABLE ALIASES - reference as {{{{alias}}}}:
{aliases}

AVAILABLE VALUE SETS - reference as {{{{name}}}}, already resolved to this customer's real category \
values (e.g. inside an IN clause):
{value_sets}
{context}
Propose up to {max_kpis} NEW KPIs that would be genuinely useful for analyzing this specific \
customer's data. Ground each one in a real pattern you can see above - do not propose a metric \
that needs a role, alias, or value set not listed.

Return strict JSON: {{"kpis": [{{"id": "snake_case_id", "label": "...", "description": "...", \
"formula_plain_english": "...", "grain": "...", "requires": {{"measures": [...], "dimensions": [...], \
"filters": [...], "entities": [...]}}, "sql": "SELECT ...", "unit": "count|currency|percent|...", \
"assertions": ["python expression over result columns, e.g. total >= 0"]}}]}}

Rules:
- `id` must be lowercase snake_case and must not collide with an id already listed above.
- `sql` must be a single SELECT statement using only the `{{{{tokens}}}}` listed above - anything \
else will simply fail to compile.
- EVERY selected column must have an explicit `AS <name>` alias - e.g. `COUNT(*) AS total_records`, \
never a bare unaliased expression. `assertions` are evaluated once per output ROW, as Python \
expressions where each alias is a variable holding that row's value for that column - an assertion \
naming a variable that isn't one of your own aliases will simply fail at runtime with a NameError. \
There is no `result`/`rows` variable - only the aliases you defined. Example matching this exactly: \
`sql: "SELECT COUNT(*) AS total_records FROM {{{{fact}}}}"`, `assertions: ["total_records >= 0"]`.
- `requires` must list every canonical role your `sql` actually references via `{{{{role}}}}` - this \
is how the system checks the KPI is satisfiable before ever running it."""


def _existing_catalog_block(pack: IndustryPack) -> str:
    return "\n".join(f"- {k.id}: {k.label} - {k.description}" for k in pack.kpis) or "(none yet)"


def _roles_block(pack: IndustryPack, bindings: SchemaBindings) -> str:
    lines = [
        f"- {{{{{c.role}}}}}: {pack.canonical_roles.get(c.role, '')} (bound to real column {c.physical!r})"
        for c in bindings.columns
    ]
    return "\n".join(lines) or "(none bound)"


def _table_aliases_block(pack: IndustryPack) -> str:
    return "\n".join(f"- {{{{{alias}}}}}: {desc}" for alias, desc in pack.table_aliases.items()) or "(none)"


def _value_sets_block(bindings: SchemaBindings) -> str:
    return "\n".join(f"- {{{{{vs.name}}}}}: {vs.values}" for vs in bindings.value_sets) or "(none resolved)"


def propose_kpis(
    pack: IndustryPack,
    bindings: SchemaBindings,
    provider: LLMProvider,
    data_context: dict | None = None,
) -> list[CanonicalKpi]:
    """Returns up to `MAX_PROPOSED_KPIS` candidates. Never raises - degrades
    to `[]` on any LLM/parsing failure, the same "informs, never blocks"
    contract every other optional AI-assisted stage in this pipeline
    follows. Callers still compile+validate every returned candidate
    themselves; nothing here is trusted as-is."""
    context_block = render_data_context(data_context)
    prompt = _PROMPT_TEMPLATE.format(
        pack_name=pack.name,
        existing=_existing_catalog_block(pack),
        roles=_roles_block(pack, bindings),
        aliases=_table_aliases_block(pack),
        value_sets=_value_sets_block(bindings),
        context=f"\nCONTEXT FROM THE BUSINESS OWNER:\n{context_block}\n" if context_block else "",
        max_kpis=MAX_PROPOSED_KPIS,
    )
    try:
        raw: Any = provider.generate_json(prompt)
    except LLMError:
        return []
    if not isinstance(raw, dict):
        return []

    existing_ids = {k.id for k in pack.kpis}
    candidates: list[CanonicalKpi] = []
    for item in raw.get("kpis", [])[:MAX_PROPOSED_KPIS]:
        if not isinstance(item, dict):
            continue
        try:
            candidate = CanonicalKpi.model_validate(item)
        except Exception:  # noqa: BLE001 - a malformed candidate is just dropped
            continue
        if candidate.id in existing_ids:
            continue
        candidates.append(candidate)
        existing_ids.add(candidate.id)
    return candidates


__all__ = ["MAX_PROPOSED_KPIS", "propose_kpis"]
