
# Agent Schema mode — scaling Forge to large databases

## Context

Forge today produces one monolithic knowledge pack per run: `config/schema_model.json`,
shipped inside the plugin and served whole through `schema://model`. That works
beautifully for 3–10 tables. It collapses on a real warehouse.

**Measured, not estimated.** `generated/runs/56c1cc676e46/.../config/schema_model.json`
is **38 KB for 3 tables / 19 columns** — ~12.6 KB per table. Extrapolated:

| Tables                               | Artifact          | Tokens          |
| ------------------------------------ | ----------------- | --------------- |
| 3 (today's fixtures)                 | 38 KB             | ~9.5k           |
| **50** (your own connector DB) | **~618 KB** | **~158k** |
| 200                                  | ~2.4 MB           | ~633k           |

And `resources.py::build_instructions` closes with a literal instruction to the model:
*"BEFORE WRITING SQL: read resource `schema://model`"* — so the agent is actively told
to pull the whole thing. On the 50-table database in `D:\agents_schema\skills\`
(`v_meta_ads_*`, `v_ga4_*`, `v_gads_*`, `v_gsc_*`, `v_ig_*`, plus 16 operational tables)
the current pipeline would not produce a usable plugin.

Artifact size is only the visible symptom. Exploration found the pipeline has **hard
blockers** well before packaging — an uncapped O(n²) relationship probe that hammers the
live database, two single-shot LLM calls that overflow context and then fail *silently*,
and a clone cap of 25 tables. Those are listed in "What actually breaks" below.

**Outcome intended:** an opt-in mode that (a) makes a 50–500 table run finish at all,
(b) ships a *searchable, sharded* catalog instead of one blob so the agent retrieves only
what it needs, and (c) emits that catalog in the open **dbt Agents Schema** shape so any
SQL-capable agent can read it — not just the plugin Forge generates.

Existing behaviour on small databases must be **bit-for-bit unchanged** when the mode is off.

---

## Verdict on the ChatGPT plan

The direction is right and the diagnosis is right. Three things are right enough to build on:

- **"Don't replace the database with Markdown."** Correct. Markdown is a projection.
- **"Context explosion — you need retrieval, not a giant prompt."** Correct, and now
  quantified above.
- **"Facts from deterministic inspection, meaning from the LLM."** Correct — and Forge
  *already* enforces this harder than the note assumes.

But the plan is written for a system that doesn't exist yet, and Forge is not that system.
Corrections:

| ChatGPT proposes                                                                             | Reality in this codebase                                                                                                                                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phases 1–3: build a schema scanner, an AI documentation agent, data profiling               | **Already built and better.** `profiling/` does per-column stats, grain inference, value-set capture, correlation/temporal/FD/redundancy mining, then `synthesis.py` fact-checks every LLM claim against the structural profile and **executes every cookbook query against real data** before keeping it. That is stronger verification than the proposal describes. |
| Phase 5: add a semantic layer (metrics, canonical formulas)                                  | **Already built.** `industry-packs/*/kpis/*.json` are canonical-role metric definitions; `compiler/` renders them to SQL deterministically and `sqlglot`-validates. The LLM never writes shipped SQL.                                                                                                                                                                     |
| Phase 7: build an "Agent Data API" of tools                                                  | **Already built.** 17 MCP tools in `server.py`.                                                                                                                                                                                                                                                                                                                               |
| Phase 6: pgvector + embeddings                                                               | **Not needed at this scale, and it is the expensive rung.** 50 tables / ~600 columns is a tiny corpus. Lexical BM25 over LLM-written meanings + synonyms, expanded along the FK graph, is enough — zero new dependencies, no API calls, works offline. Revisit only if it measurably falls short.                                                                              |
| §21 "add provenance/confidence to metadata"                                                 | **Already there:** `ColumnDoc.confidence`, `CookbookEntry.verified`, `SchemaModel.coverage`, `schema_hash`.                                                                                                                                                                                                                                                             |
| A 9-phase "Agentic Data Platform" with a metadata DB, vector store, and separate API service | **A rebuild.** The actual bottleneck is a handful of uncapped loops, two oversized prompts, and three functions in `resources.py`.                                                                                                                                                                                                                                            |

**What ChatGPT missed entirely, and is the single highest-value fix:** Forge **never reads
`information_schema`**. Not once, anywhere in the repo. Every relationship is *re-derived
statistically* by value-overlap probing — which is both the most expensive pass in the
pipeline and less correct than the declared foreign keys sitting unread in the catalog.
`ColumnRole.FOREIGN_KEY` is a **dead enum member**: declared, given a meaning string, never
assigned by anything. The runtime pays for this at query time too — `record_tools.py:126`
returns a user-visible *"Could not automatically determine foreign key from X to Y."*

Reading the catalog is cheap, deterministic, fixes correctness, removes the worst scale
cost, and is the precondition for FK-graph clustering. It is the keystone of this plan.

---

## What dbt's Agents Schema actually is, and what we take

Verified against `D:\agents_schema` (v0.0.11) and the published spec. It is **a set of
physical tables in a warehouse schema named `AGENTS`**, written by a CLI that parses
*existing* artifacts — dbt `manifest.json`, LookML, OSI YAML, Sigma, Omni. Not an API, not
a file format. Its own README positions it as *"closest in spirit to `information_schema`,
but extensible across many providers."*

The core is genuinely small — `AGENTS.ROOT(provider, key, content)` and
`AGENTS.SKILL_USE(provider, skill_key, use_kind, object_ref)` are 3 and 4 columns. The
semantic content lives in the OSI family.

**The important asymmetry: dbt's spec assumes you already have the metadata.** It is a
*publisher*, not a producer. Every source it supports is hand-authored upstream. Forge
*derives* the same shape from a raw database with zero hand-authoring — and verifies it.
So Forge is the producer that Agents Schema presumes exists, and emitting `AGENTS.*` makes
Forge's output readable by any SQL agent rather than only the plugin it generates.

**Forge is strictly richer than the spec.** Confirmed absent from `AGENTS.*`: any
provenance, confidence, freshness, column data type, or relationship cardinality. Forge has
all of them. They ride in `ai_context` / `custom_extensions`, which the spec explicitly
allows.

Mapping is near-mechanical:

| Forge                                                                               | Agents Schema                                                                            |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `SchemaModel.overview`, `caveats`                                               | `ROOT(provider='forge', key='overview')`                                               |
| `TableDoc` (name, purpose, role, grain_prose)                                     | `OSI_DATASET` (+ `primary_key` from the new catalog pass)                            |
| `ColumnDoc` (name, meaning, enum, example, confidence)                            | `OSI_FIELD` (dtype + confidence → `ai_context`)                                     |
| `RelationshipDoc` (`from_ref`/`to_ref` are `"table.column"`, split cleanly) | `OSI_RELATIONSHIP` (strength + cardinality → `ai_context`)                          |
| compiled KPIs (`kpi_defs.json`)                                                   | `OSI_METRIC` (`expressions[].dialect = ANSI_SQL`)                                    |
| `CookbookEntry` (verified NL→SQL)                                                | `ROOT(key='cookbook/<slug>')` — the spec has no equivalent; this is a Forge extension |
| generated skills                                                                    | `ROOT(key='skill/<name>')` + `SKILL_USE` rows from `uses:` frontmatter             |

We adopt the **shape and the `uses:` frontmatter convention**, not the `agents-schema`
package. (Its Postgres writer is uncommitted local work in your checkout; upstream v0.0.11
supports only Snowflake/Databricks/BigQuery.)

One thing worth copying verbatim: `SKILL_USE` is **advisory** in the spec — a lookup index,
not a grant. Forge already has real enforcement in `security/allowlist.py`, so our
`SKILL_USE` rows describe what `allowed_tables` enforces. That is a genuine improvement and
should be stated in the emitted `ROOT` docs.

---

## What actually breaks at 50–500 tables

Ordered by severity. Items 1–4 are hard blockers — the run does not finish or silently
produces an empty pack.

1. **`_synthesize_global` (`profiling/synthesis.py`) is ONE LLM call** carrying every
   table's columns, every documented column, all relationships, all patterns, all quality
   findings. It overflows, raises `LLMError`, and the handler sets `raw = {}` → **empty
   overview, no caveats, no patterns, no cookbook, no error surfaced.**
2. **`profiling/semantic.py::run_semantic_profile` is the same one-call shape and is NOT
   wrapped in try/except** → hard-fails the whole run at PROFILE.
3. **`relationships.py`** generates candidates as O(PK candidates × every column in every
   other table); each survivor fires a `COUNT(DISTINCT …) FILTER (WHERE x IN (SELECT …))`
   **against the live database**. No cap, no row guard, no timeout. Worst offender.
4. **`warehouse.py: MAX_CLONE_TABLES = 25`** rejects a 200-table clone outright.
5. **Per-table synthesis failure is silent** — `_one()` swallows the exception, `{}`
   backfills every column at `confidence="low"`. A run where 60 tables timed out looks
   identical to a clean one except for a coverage number.
6. **`_MIN_SCHEMA_COVERAGE = 1.0`** counts only non-`low` confidence columns, so at scale
   every run WARNs and the signal becomes noise.
7. Query-volume blowups: exact `COUNT(*)` per table at ingest (full scan on Postgres); one
   `SELECT DISTINCT` per non-numeric column in `structural.py`; up to 35 `GROUP BY` probes
   per table in `grain.py`; uncapped column count in `valuesets.py`; O(8²) FD + redundancy
   probes per table in `patterns.py`.
8. **`build_instructions` emits one grain line per table, uncapped**, into the MCP
   `instructions` string that lands in context on every request.
9. `describe_data()` returns flat deduped column-name lists across all tables — thousands
   of ambiguous bare names in one response.
10. `_schema_hash` includes per-column cardinality and null%, so the synthesis cache misses
    on essentially every live-database run.
11. `pick_primary_table` returns **one** fact table; a warehouse has many.
12. `schema_drift_report` runs a `DESCRIBE` per table on every server start.

---

## Locked decisions

Confirmed with the user before planning:

1. **Catalog lives in both places.** Sharded files under `config/agents/` in the plugin are
   the source of truth (self-contained, offline, matches today's delivery). Real `AGENTS.*`
   Postgres tables are *additionally* written into the **Forge warehouse** when
   warehouse/clone mode is on. Forge only ever has read-only access to a customer's own
   database, so it can never write `AGENTS.*` there.
2. **Radio on the connect form**, like the existing live/clone radio — "Full knowledge pack"
   vs "Agent Schema (retrieval)". Auto-preselected to Agent Schema above a table-count
   threshold, with a note saying why; always user-overridable.
3. **Lexical BM25** over the catalog, expanded along the verified FK graph. No embeddings,
   no new dependencies, works offline.
4. **FK-graph clusters, LLM-named**, for the domain mini-catalogs. Tables classified
   `role=staging|log` (`alembic_version`, `oauth_states`, `sync_errors`) are dropped rather
   than lumped in — which is what a pure name-prefix grouping gets wrong.

---

## Evidence: retrieval only works over LLM-written synonyms

Prototyped BM25 against your real 50-table schema (extracted from
`D:\agents_schema\skills\*.md`) to check the locked retrieval decision before building it.

Indexing **table names + column names only** — which is all your `introspect_and_generate_skills.py`
produces — gets 4 of 5 queries right and fails hard on the fifth:

```
Q: which google ads keywords get the most clicks
     5.51  public.v_meta_ads_campaign_insights     <- WRONG (meta, not google)
     5.07  public.v_meta_ads_adset_insights        <- WRONG
     5.03  public.v_meta_ads_ad_insights           <- WRONG

Q: compare paid search vs paid social cost
     6.00  public.v_ga4_new_vs_returning           <- WRONG
```

Cause: `"google ads"` does not lexically match `v_gads_`. Adding one line of LLM-written
synonyms per table (`google ads adwords paid search …` / `facebook instagram paid social …`)
fixes both:

```
Q: which google ads keywords get the most clicks
     4.17  public.v_gads_campaign_performance      <- right family
     4.08  public.v_gads_keyword_performance       <- correct table

Q: compare paid search vs paid social cost
     6.93  public.v_gads_campaign_performance      <- correct
     6.91  public.v_gads_keyword_performance
```

**Two conclusions that shape the plan:**

1. **`synonyms` must be added to `TableDoc` and `ColumnDoc`.** `SchemaModel` has no synonyms
   field today. Without it, lexical retrieval fails exactly where a warehouse's abbreviated
   naming needs it most. This is the one genuinely new piece of semantic content the LLM
   must produce, and it maps directly onto `OSI_*.synonyms` in the target format.
2. **Retrieve top-K and let the model choose; never trust top-1.** Even with synonyms the
   correct table was ranked 2nd. The design returns K≈8 candidates plus their FK
   neighbours, which is what the schema-linking literature (RASL, CRED-SQL) does at
   1000+ tables.

This also shows why Forge is the right producer: the synonyms come from the verified
synthesis pass. A pure catalog scraper cannot generate them.

---

## Design

One flag, `agent_schema: bool = False`, threaded exactly like the existing `use_agent`
precedent (`cli.py:101` → `run_pipeline(use_agent=)` → `resolve_bindings(use_agent=)`).
Off ⇒ every code path below is skipped and output is byte-identical to today.

The mode changes three things: **the catalog is read**, **the work is budgeted**, and **the
knowledge pack ships sharded + searchable instead of whole**.

### Step 1 — Read `information_schema` (the keystone)

New `packages/forge-core/src/forge_core/ingestion/catalog.py`:

```python
@dataclass(frozen=True)
class ForeignKeyFact:
    from_table: str; from_columns: list[str]
    to_table: str;   to_columns: list[str]; constraint_name: str

@dataclass(frozen=True)
class CatalogFacts:
    primary_keys: dict[str, list[str]]        # table -> pk columns
    foreign_keys: list[ForeignKeyFact]
    unique_keys: dict[str, list[list[str]]]
    not_null: dict[str, set[str]]
    comments: dict[str, str]                  # "table" / "table.column" -> comment
    views: set[str]

def read_catalog(con, *, catalog: str, schema: str, kind: SourceKind) -> CatalogFacts
```

- **Postgres** — `SELECT * FROM postgres_query('<attached>', $$ … $$)`, verified available
  (`postgres_query` is a real DuckDB function). Four passthrough queries: PK/unique from
  `table_constraints` ⋈ `key_column_usage`; FKs adding `constraint_column_usage`; comments
  from `pg_description`; views from `information_schema.views`.
- **SQLite** — `sqlite_query(db, 'PRAGMA foreign_key_list(<t>)')` / `pragma_table_info`.
- **Files (CSV/Parquet/Excel)** — no catalog exists; returns an empty `CatalogFacts` and
  everything falls back to today's heuristic unchanged.

Wire-up:

- `DataSource` gains `catalog: CatalogFacts | None = None` (additive, defaults to None).
- `relationships.py::detect_relationships(..., catalog=None)`: a declared FK becomes a
  `RelationshipCandidate(strength="strong", source="declared")` with **no probe at all**.
  The value-overlap heuristic then runs only over pairs the catalog did not already cover,
  under the Step-2 budget. `RelationshipCandidate` gains
  `source: Literal["declared","inferred"] = "inferred"`.
- `structural.py::_guess_role` assigns `ColumnRole.FOREIGN_KEY` for declared FK columns —
  filling the dead enum member.
- `packages/mcp-runtime/.../tools/record_tools.py:126` reads declared FKs, eliminating the
  user-visible *"Could not automatically determine foreign key"*.

This is the highest-leverage change in the plan: it removes the worst scale cost, makes the
join graph **deterministic rather than inferred**, and is the basis the FK clustering in
Step 7 needs.

### Step 2 — Budget every O(n²) pass

New `profiling/budget.py` holding a `ProfilingBudget` dataclass. Defaults reproduce today's
behaviour exactly; a tightened budget is selected only in agent-schema mode.

| Pass                 | Cap                                                                                                |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| `relationships.py` | max inferred probes (declared FKs are free and uncapped); skip probing tables over a row threshold |
| `valuesets.py`     | max**columns** overall (today: unbounded)                                                    |
| `grain.py`         | skip composite-key brute force above a table count; keep single-column                             |
| `patterns.py`      | max tables mined, prioritised by cluster centrality                                                |
| `structural.py`    | batch the per-column`SELECT DISTINCT` round-trips                                                |

### Step 3 — Chunk the LLM passes and make failure loud

Verified in source: `synthesis.py:359-361` and `:221-224` both do
`try: … except LLMError: raw = {}` — an overflow silently yields an empty pack.
`synthesis.py:327` puts `[c.model_dump() for c in doc.columns]` for **every** table in one
prompt; `:300` asks for *"15-40 cookbook entries: at least one per table"*.

- `_synthesize_global` becomes map-reduce: chunk tables to a token budget, one call per
  chunk for patterns/caveats/cookbook, then one small reduce call for the overview.
- `profiling/semantic.py::run_semantic_profile` gets the same chunking **and** a try/except
  (today it has none and hard-fails the run).
- Per-table synthesis failures accumulate into `SchemaModel.synthesis_failures: list[str]`,
  logged at PROFILE and raised as a harness WARN. Silent degradation becomes visible.
- Cookbook target scales per *cluster*, not per table.
- `_MIN_SCHEMA_COVERAGE` measured over retained business tables, not plumbing.
- `_schema_hash` drops per-column `cardinality`/`null_percent` (keeps dtype + role) so the
  cache survives ordinary data churn; bump `synth_version` to 3.

### Step 4 — Add `synonyms`

`TableDoc.synonyms: list[str]` and `ColumnDoc.synonyms: list[str]`, additive with
`default_factory=list`, requested in the synthesis prompt. Required by the retrieval
evidence above and maps straight onto `OSI_*.synonyms`.

### Step 5 — The sharded catalog artifact

New `packaging/agents_catalog.py` writing:

```
config/agents/
  root.json           # overview, caveats, counts, skills index, schema_hash
  index.json          # prebuilt BM25: {N, avgdl, df:{term:n}, docs:[{ref,kind,len,tf}]}
  tables/<name>.json  # one TableDoc + columns + that table's value_sets + quality findings
  relationships.json  # incl. source: declared|inferred
  metrics.json        # compiled KPIs, OSI_METRIC shape
  cookbook.json
  clusters.json       # FK-graph domains -> member tables
```

**Backward compatibility both ways.** With the mode ON we still write a *trimmed*
`schema_model.json` (overview, caveats, counts — no per-table bodies) so an older runtime
starts and behaves sanely. With the mode OFF nothing under `config/agents/` is written and
`schema_model.json` is exactly what it is today.

### Step 6 — Runtime retrieval

- `config.py`: load `config/agents/` when present into `RuntimeConfig.catalog: … | None`.
  Absent ⇒ `None` ⇒ every existing tool behaves as today. (`schema_model.json` is already
  optional there, so this follows the established pattern.)
- New `mis_mcp_runtime/catalog/search.py` — ~60 lines of **pure-Python BM25** over the
  prebuilt `index.json`. Deliberately *not* DuckDB FTS: `INSTALL fts` triggers a network
  download on first use (measured: 28 s), which is unacceptable inside an offline customer
  plugin. The corpus is thousands of short docs; Python scoring is instant.
- New tools in `server.py` (same `@_tool` decorator, `_META` annotation):

```python
search_tables(query: str, limit: int = 8) -> dict
search_columns(query: str, limit: int = 20, table: str | None = None) -> dict
get_table_doc(table: str) -> dict            # lazy per-table; replaces reading the whole model
find_join_path(from_table: str, to_table: str) -> dict   # BFS, reuses _join_chains logic
search_cookbook(query: str, limit: int = 5) -> dict
list_domains() -> dict
```

- `resources.py`: add `schema://table/{name}` and `schema://domains`; keep every existing
  URI. In agent-schema mode `schema://model` returns the trimmed model plus a pointer to
  `search_tables`, not 633 KB.
- `build_instructions`: cap the currently-uncapped per-table grain lines (list **domains**
  above a threshold), and change the closing directive from *"read resource schema://model"*
  to *"call `search_tables()` first"*. This one line is what stops the agent pulling the
  whole pack.

### Step 7 — FK-graph clusters become skills

New `generation/domains.py`: connected components over the (now largely declared)
relationship graph, dropping tables whose `TableDoc.role` is `staging` or `log` — which is
what puts `alembic_version` / `oauth_states` / `sync_errors` out of the analyst's way
instead of in `core_tables` beside `organizations`. The LLM names and describes each
cluster. Each becomes `skills/<domain>/SKILL.md` carrying dbt-style `uses:` frontmatter.

### Step 8 — `AGENTS.*` warehouse projection

New `publishing/agents_schema.py::publish_agents_schema(admin_url, schema, catalog)` —
creates and fills `ROOT`, `SKILL_USE`, `OSI_DATASET`, `OSI_FIELD`, `OSI_METRIC`,
`OSI_RELATIONSHIP` in the **Forge warehouse** via the existing `postgres_execute` path in
`ingestion/warehouse.py`. Runs only when warehouse/clone mode is on. Forge's extra fields
(confidence, dtype, cardinality, `verified`) go in `ai_context` / `custom_extensions`, which
the spec allows. We implement the ~10 tables directly rather than depending on the
`agents-schema` package (upstream v0.0.11 has no Postgres writer).

### Step 9 — Wiring

- `agent_schema: bool` on `run_pipeline`, `CreateRunFromPathRequest`, `RunDetail`.
- Radio in `apps/web/src/pages/Wizard.tsx::ConnectForm`, mirroring the existing live/clone
  radio; pre-selected above the table threshold with a one-line reason.
- Raise / make overridable `warehouse.py: MAX_CLONE_TABLES = 25` (a 200-table clone is
  rejected outright today).
- Docs: `docs/architecture.md`, `docs/generator-flow.md`, new `docs/agent-schema.md`.

---

## Phasing

**Phase 1a — makes a large run finish at all.** Steps 1, 2, 3, and the `agent_schema` flag
from Step 9. No new artifact, no new tools. After this a 50-table run completes and reports
honestly instead of silently emitting an empty pack. This is independently valuable and is
the natural stopping point if the rest slips.

**Phase 1b — makes the output usable.** Steps 4, 5, 6. The sharded catalog and retrieval
tools. This is where the 633 KB → a few KB per query win lands.

**Phase 1c — the standard and the domains.** Steps 7, 8, and the UI radio.

**Phase 2 — deferred, not built here.** Embeddings/hybrid retrieval; multi-schema
enumeration; query-log learning; multiple fact tables per run (`pick_primary_table` returns
one today); per-request DuckDB connections (there is one global `_QUERY_LOCK`); incremental
re-profiling; freshness columns.

---

## Verification

- **Catalog pass** — new `packages/forge-core/tests/test_catalog.py`: against a live
  Postgres (gated on `FORGE_TEST_WAREHOUSE_ADMIN_URL`, as `test_warehouse.py` already does)
  create two tables with a declared FK; assert `read_catalog` returns it, that
  `detect_relationships` emits it with `source="declared"` and **fires zero probe queries**,
  and that a file source still produces today's inferred relationships unchanged.
- **Budget** — assert a synthetic 200-table profile completes under a probe-count ceiling.
- **Chunking** — a `FakeLLMProvider` that raises on any prompt over N tokens must still
  yield a non-empty overview and a non-empty cookbook; and a provider that fails on 3 of 20
  tables must surface those 3 in `synthesis_failures`.
- **Retrieval** — unit-test `search_tables` against the real 50-table fixture and assert
  "google ads keywords" ranks a `v_gads_*` table in the top 3 (it does not today; see
  Evidence).
- **Backward compatibility** — the existing golden regression and
  `tests/e2e/test_genericity.py` must pass untouched with the flag off; byte-compare a
  generated plugin before/after to prove the off-path is unchanged.
- **Old runtime / new plugin** — point the current `mis-mcp-runtime` at a `config/agents/`
  plugin and confirm it starts on the trimmed `schema_model.json`.
- **End to end** — run against your 50-table Postgres with `agent_schema: true`; confirm
  the run completes, `config/agents/` is written, `search_tables("meta ads spend")` returns
  the right family, `claude plugin validate --strict` passes, and the warehouse carries
  `AGENTS.*` rows.
- **Gates** — `uv run pytest packages/forge-core/tests packages/mcp-runtime/tests`,
  `uv run pytest apps/api`, `apps/web` tsc + vitest + build, ruff + mypy on changed files.

## Constraints

- **Nothing is committed.** Work continues on `improvement-roadmap`, which already carries
  99 uncommitted files from the roadmap/redesign/clone efforts.
- No change to existing behaviour when `agent_schema` is off — enforced by byte-comparison.
- No new runtime dependency; the shipped plugin must still work offline.
- Forge keeps read-only access to customer databases; `AGENTS.*` is only ever written to the
  Forge-owned warehouse.
- `.env` currently points `FORGE_CLIENT_WAREHOUSE_URL` at a live Supabase instance, so any
  warehouse-mode test run creates real schemas there and must be cleaned up via
  `DELETE /runs/{id}`.
