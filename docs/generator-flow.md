# Generator flow — stage by stage

This walks through one call to
[`run_pipeline`](../packages/forge-core/src/forge_core/orchestrator.py),
which is what both `forge run` (CLI) and `POST /runs` (API) invoke. Every
stage logs a `StageEvent` onto the `RunRecord` it's given, so a caller can
render identical progress regardless of which front end started the run.

## 0. Inputs

- `record.source_path` — a file, a directory of files (multi-table), or a
  SQLite database.
- `record.industry_override` — optional; skips classification.
- `packs_root` — defaults to `industry-packs/` at the repo root
  (`DEFAULT_PACKS_ROOT`).
- `profiling_provider` / `generation_provider` / `critique_provider` — three
  independently-selectable `LLMProvider`s (`forge_core.llm.get_provider`).
  All three are **required**: `run_pipeline` fails fast if any is `None`.
  Tests pass `forge_core.testing.FakeLLMProvider` (deterministic, keyless).

## 1. Ingest — `forge_core.ingestion.registry.ingest`

Dispatches on the source path to `SqliteAdapter` or `FileAdapter`
(`forge_core/ingestion/`). Every adapter registers its tables as DuckDB
views (`duckdb_attach_sql` on each `Table`) so every later stage queries
through one uniform SQL surface regardless of the original format. Returns
a `DataSource` (`forge_core/models/datasource.py`).

## 2. Profile — `forge_core.profiling.build_schema_profile`

- **Structural** (deterministic): per-column dtype, cardinality, null rate,
  a `ColumnRole` guess; relationship detection (name-variant candidates +
  value-overlap verification, `strong`/`weak`, empty if unrelated);
  single- and composite-column grain; full value sets for low-cardinality
  columns; statistical patterns (`patterns.py`: correlation, temporal
  trend/seasonality, functional dependencies, redundant columns).
- **Semantic** (deterministic samples, no redaction): merges back per-column
  meaning annotations and dataset-level insights.

Output: `SchemaProfile` (`forge_core/models/schema_profile.py`).

## 2b. Synthesize — `forge_core.profiling.synthesis.build_schema_model`

Mandatory LLM pass. Per-table docs (purpose/role/grain, column meaning +
enum decode) then a global pass (overview, caveats, pattern notes, a
NL→SQL cookbook). Fact-checked against the structural profile; every
cookbook query executed once against the real data. Cached on disk by a
hash of the structural facts. Stored on `RunRecord.schema_model`, shipped
as `config/schema_model.json`. Output: `SchemaModel`
(`forge_core/models/schema_model.py`).

## 3. Classify — `forge_core.classification.classify`

Loads every pack under `packs_root` (`load_all_packs`) and scores each
pack's `signatures.json` against the profile: entity-name hints matching
table names, column-name hints matching column names, required canonical
roles present in the data, and table count within the pack's expected
range. Returns a `ClassificationResult` with `ranked_matches`.

If the top match's confidence is below the auto-accept threshold and no
`industry_override` was supplied, the orchestrator sets
`RunStatus.NEEDS_INPUT` and returns immediately — nothing downstream runs
yet. The caller (CLI: re-run with `--pack`; API:
`POST /runs/{id}/confirm-industry`) sets `industry_override` and calls
`run_pipeline` again with the *same* `RunRecord`, which re-runs ingest/
profile/classify (cheap, deterministic) and then proceeds past the pause.

## 4. Bind — `forge_core.binding.resolve_bindings`

For the chosen pack, resolves every canonical role its KPIs need to a real
`table.column`:

1. **Deterministic scorer** (`binding/scorer.py`) — pack-authored
   `role_hints`, core-token overlap between the role name and column names,
   and structural type compatibility (a role that needs `currency` won't
   bind to a `free_text` column).
2. **LLM proposer** (`binding/llm_proposer.py`) — for anything still
   unresolved, if a provider was passed.
3. **Overrides** — `binding_overrides: dict[canonical_role, "table.column"]`
   always wins, whether supplied up front or via
   `POST /runs/{id}/bindings` after seeing `unresolved_roles`.

Output: `SchemaBindings`, including `denied_columns` (anything the pack
says must never be projected — see [security.md](security.md)).

## 5. Compile KPIs — `forge_core.compiler.compile_all`

For each `CanonicalKpi` in the pack, renders its Jinja-style SQL template
using the resolved bindings, then parses and validates the result with
`sqlglot` (dialect `duckdb`). A KPI whose required roles didn't resolve is
recorded in `KpiDefsFile.skipped` rather than failing the whole run. Output:
`KpiDefsFile` — the *only* thing the MCP runtime ever executes.

## 6. Generate — `forge_core.generation.generate_plugin_content`

Produces every prose/config artifact, each constrained to the plugin
spec's allow-listed frontmatter fields (`forge_core/models/plugin_spec.py`)
so nothing generated can violate §[plugin-format.md](plugin-format.md) by
construction:

- `skills.py` → `SKILL.md` with a KPI catalog and guardrail notes.
- `agents.py` → one deep-dive subagent wired to the bundled MCP tools.
- `recipes.py` + `commands.py` → one slash command per recipe (default: run
  every KPI and summarize).
- `hooks.py` → a `SessionStart` hook that reminds the model of guardrails.
- `artifacts.py` → a static HTML KPI dashboard, computed by *actually
  running* the compiled SQL against the real data.

## 7. Validate — `forge_core.validation.run_harness`

Eight checks; see [security.md](security.md) for what each one is for.
Any hard `fail` sets `RunStatus.FAILED` even though packaging already
happened — the plugin is on disk for inspection but the run is not
considered successful.

## 8. Package — `forge_core.packaging`

`build_plugin_spec` assembles everything above into a `PluginSpec`;
`write_plugin` serializes it as BOM-less UTF-8 files, bundles
`mis-mcp-runtime` (`packaging/mcp_bundle.py`), and copies the original data
files into `data/` so the bundled runtime can resolve
`${CLAUDE_PLUGIN_ROOT}`-relative paths at install time.

## 9. Publish — `forge_core.publishing` (`forge publish ...` / not yet wired into the API)

- `local.publish_local` — copy or zip to a path.
- `github.push_plugin_to_repo` — one commit via the Git Data API.
- `marketplace.publish_to_marketplace` — copy into a marketplace checkout
  and rewrite its catalog.
