# Authoring an industry pack

The industry packs are the moat: curated, per-vertical KPI knowledge that the
generic generator reuses across every customer in that vertical. `forge packs`
is the tooling to build and iterate on one without a full generation run and
without an LLM.

## Commands

```bash
forge packs new <slug>              # scaffold industry-packs/<slug>/
forge packs lint <slug>             # semantic checks beyond the JSON schema
forge packs test <slug> <dataset>   # bind + compile + dry-run against real data
forge packs validate [root]         # load every pack (pydantic validation)
```

All take `--root` to point at a packs directory other than `industry-packs/`.

## `new`

Creates `industry-packs/<slug>/` with a starter `pack.json` (empty
`canonical_roles` / `signature`, `guardrails.denied_role_categories =
["free_text"]`), one example KPI under `kpis/`, and empty
`skills/` `agents/` `recipes/` directories. Fill in:

- `canonical_roles` — the vertical's vocabulary, in role terms, never physical
  column names (e.g. `revenue_amount`, `transaction_status`).
- `role_hints` — substrings that identify each role in a real schema.
- `signature` — the classifier fingerprint (`entity_name_hints`,
  `column_name_hints`, `required_role_categories`, `table_count_range`). Keep
  it conservative; a pack that outranks a better match is worse than one that
  falls through to `generic-analytics`.
- `kpis/*.json` — one KPI per file. `sql` is a `{{token}}` template over roles,
  `{{fact}}`, and value-set names; the compiler renders it against a
  customer's bindings.

## `lint`

Checks that pydantic can't: every role a KPI `requires` exists in
`canonical_roles`; every `{{token}}` in a KPI's `sql` is a real role / alias /
value-set; `role_hints` keys are real roles; `signature.table_count_range` is
ordered; each KPI `sql` looks like a `SELECT`/`WITH` with a `FROM`; each
`assertion` references only that KPI's own result columns; `requires_roles` on
skill/agent/recipe template refs are real roles. Exit 1 on any `error`.

## `test`

The full deterministic path against one dataset:
`ingest → build_structural_only → score_pack → resolve_bindings → compile_all
→ dry-run`. Prints the classifier match score, the per-role bind (column,
confidence, `deterministic` / `cross_table`), the unresolved roles, and every
KPI's compile + dry-run result. Exit 1 if any KPI is skipped or errors on the
dry-run. This is the fast iteration loop — change `role_hints` or a KPI, re-run.

## Demand signal

Every API run records `primary_pack_slug`, `pack_confidence`, `kpis_compiled`,
and `kpis_skipped` on the `runs` row (and the `forge_api.pipeline` log emits a
`pack-fit …` line). Aggregate over `runs` to see which verticals keep landing
on `generic-analytics` or skipping KPIs — that is unmet demand, and the queue
for the next pack.
