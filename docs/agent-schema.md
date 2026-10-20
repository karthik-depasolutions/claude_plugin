# Agent Schema mode — large databases

`agent_schema` is an **opt-in** run mode for databases that are too big for the
default pipeline: roughly 25–500 tables. It is off by default and, when off,
output is byte-for-byte what it was before this mode existed (proven by
`tests/e2e/test_genericity.py` and a shard-writing gate keyed on the flag).

Turn it on with:

- CLI: `forge run <source> --agent-schema`
- API: `POST /runs { "agent_schema": true }` or `POST /runs/upload?agent_schema=true`
- Wizard: the **Agent Schema mode** checkbox on the connect form

## Fast / no-LLM test run

To exercise the whole pipeline against a large database quickly and for **$0**:

- CLI: `forge run <source> --agent-schema --no-llm`  (or `--fast` to keep a real LLM)
- API: `POST /runs { "agent_schema": true, "no_llm": true }`
- Wizard: the **Fast test run (no LLM)** checkbox

`no_llm` swaps a deterministic stub for every LLM role (no API calls), skips the
clarification pause, and — with `agent_schema` — selects `FAST_AGENT_SCHEMA_BUDGET`:
declared PK/FK only, **zero** inferred relationship probes, no value-set scans,
no composite-grain probing, no pattern mining, no quality analysis. A 70-table
remote database profiles in ~2 minutes instead of ~15. `FORGE_AGENT_SCHEMA_FAST=1`
forces the fast budget for a real-LLM run too.

The generated plugin is structurally complete (catalog, shards, BM25 index,
relationships, domains, retrieval tools all present and validated) but its prose
is template text and it has no verified cookbook — it's for checking the
pipeline, not for shipping.

## Why it exists

The default pipeline breaks in three ways on a warehouse-sized schema:

1. **Relationship detection is O(n²).** Every PK candidate is value-overlap
   probed against every column of every other table — hundreds of full-column
   scans against the live DB, uncapped.
2. **The knowledge pack is one blob.** `config/schema_model.json` is ~12 KB per
   table; at 200 tables that is ~2.4 MB the runtime loads whole and the model is
   told to read on every request.
3. **Two LLM passes are single-shot.** `run_semantic_profile` and the global
   synthesis call each carry every column of every table and silently produce an
   empty pack (or fail the run) when they overflow.

## What the mode changes

| Area | Default | `agent_schema` |
|---|---|---|
| Declared keys | never read | `information_schema` / `PRAGMA` read once (`ingestion/catalog.py`); declared FKs become relationships with **zero probe queries**, declared FK columns get `ColumnRole.FOREIGN_KEY` |
| Profiling budgets | unbounded | `AGENT_SCHEMA_BUDGET` (`profiling/budget.py`) caps inferred relationship probes (500), value-set columns (200), composite-grain tables, pattern-mined tables (30) |
| Semantic pass | single call, can fail the run | wrapped (`semantic=None` on error) and skipped above 1200 columns |
| Global synthesis | one call, silent empty on overflow | chunked map-reduce; per-table failures recorded in `SchemaModel.synthesis_failures` and WARNed by the harness |
| Knowledge pack | `config/schema_model.json` (whole) | **also** `config/catalog/` — sharded `TableDoc`s + a prebuilt BM25 index + `relationships.json` + `cookbook.json` + FK-graph `domains` |
| Runtime | reads `schema://model` | `search_schema()` → `get_table_schema([...])` → `find_join_path()`; `schema://model` demoted to a pointer; `list_domains()` for orientation |
| Column meaning | `meaning` prose | plus `synonyms` on every table and column (drives retrieval; a warehouse's `v_gads_*` only matches "google ads" via synonyms) |

## `config/catalog/` layout

```
config/catalog/
  index.json          table listing + BM25 idf weights + shard manifest + domains
  shard_0000.json     ~20 full TableDoc entries (name, purpose, grain, columns, synonyms)
  shard_0001.json     ...
  relationships.json  RelationshipDoc list (source: declared | inferred)
  cookbook.json       verified NL->SQL entries
```

Written by `forge_core.packaging.catalog_shard.write_catalog_shards`, from the
**redacted** `schema_model` dict (denied-column values are stripped, same as
`schema_model.json`). Read at runtime by `mis_mcp_runtime.catalog.Catalog` —
pure-Python BM25, lazy per-shard load, no `forge_core` import, works offline.

## `AGENTS.*` projection (interop)

The knowledge pack is *also* projected into the open
[dbt Agents Schema](https://github.com/dbt-labs/agents_schema) shape —
`AGENTS.ROOT`, `AGENTS.OSI_DATASET`, `AGENTS.OSI_FIELD`,
`AGENTS.OSI_RELATIONSHIP`, `AGENTS.OSI_METRIC`, `AGENTS.SKILL_USE`
(`forge_core.publishing.agents_schema`) — so any SQL-capable agent can
discover the customer's tables without the Forge plugin. Two targets:

- **Forge warehouse** — for an `agent_schema` run whose data is
  warehouse-backed (upload-to-warehouse, or `clone: true`). Unconditional;
  Forge owns that database.
- **The customer's own source database** — for a live-Postgres run started
  with `publish_agents_to_source: true` (API/wizard opt-in, off by default).
  Writes only the `AGENTS` schema, using the supplied read credential; if
  that role can't `CREATE`, it's logged and skipped. See `docs/security.md`
  → "The one write path". Independent of `agent_schema` mode.

Strictly best-effort either way: the file `config/catalog/` is the source of
truth, and a projection failure never fails the run. Forge's richer fields
(confidence, enum decode, relationship strength) ride in `ai_context` /
`custom_extensions`, which the spec allows.

Re-publish for an existing run:

```bash
uv run python -m forge_core.publishing.agents_schema  # (or the ops script)
```

## What is NOT in this mode (deferred)

Embeddings / hybrid retrieval; multi-schema enumeration; LLM-named domains
(clustering is deterministic — connected components, hub-named, `staging`/`log`
tables dropped); query-log learning; multiple fact tables per run.

## Verification

`packages/forge-core/tests/test_agent_schema.py`,
`test_agents_schema_projection.py`, and
`packages/mcp-runtime/tests/test_catalog_runtime.py` cover the catalog read, the
probe budget, synonym-driven BM25 ranking, the sharded layout, synthesis-failure
visibility, the runtime `Catalog`, and the `AGENTS.*` SQL projection. The
flag-off path is held byte-stable by `tests/e2e/test_genericity.py`.
