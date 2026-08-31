# Security model

## The core boundary: the LLM never writes code that ships

Gemini is used in three roles (`forge_core.llm.get_provider(role=...)`):
`profiling` (semantic column annotations), `generation` (prose for skills/
agents/commands, and proposing novel KPI SQL *templates*), and `critique`
(self-review). In every role, its output is either:

- **prose**, reviewed by the `self_critique` check, or
- a **canonical-role binding proposal**, which the deterministic compiler
  (`forge_core.compiler`) turns into concrete SQL and `sqlglot`-validates —
  the LLM's proposed binding never reaches a customer as executable SQL
  without passing through the compiler and the `sql_safety` + `dry_run`
  checks first.

The generic MCP runtime (`packages/mcp-runtime/`) never executes anything
the LLM wrote directly — it only executes `config/kpi_defs.json`, which is
compiler output, not LLM output.

## Profiling: what reaches the LLM

`forge_core.profiling.semantic` and `forge_core.profiling.synthesis` send
the LLM **column metadata (name, dtype, cardinality, a structural role
guess), full value sets for low-cardinality columns, statistical pattern
summaries, and a capped sample of real row values** — never a full column
dump. The `is_likely_pii` heuristic and the sample-redaction step were
removed: sample values now reach the model unmasked. The understanding
phase is mandatory, so there is no "omit the provider" path.

## The validation harness — seven checks, `harness.py`

Every check produces a `ValidationCheckResult` (`pass|warn|fail|skipped`);
a hard `fail` on any of them blocks a run from being considered
`SUCCEEDED` even though the plugin was already packaged to disk for
inspection (`forge_core.orchestrator`).

1. **`fact_check`** (`validation/facts.py`) — every table/column binding
   must point at something that actually exists in the profiled
   `SchemaProfile`; an unresolved canonical role required by a non-optional
   KPI is a hard error.
2. **`sql_safety`** (`validation/sql_safety.py`) — re-parses every compiled
   KPI with `sqlglot`; rejects anything that isn't a bare `SELECT`/`WITH`,
   rejects `SELECT *`, enforces the pack's table allow-list, and rejects any
   projection of a `denied_columns` entry.
3. **`dry_run`** (`validation/dry_run.py`) — actually executes every
   compiled KPI against the real (or sampled) data in DuckDB and evaluates
   each KPI's `assertions` (e.g. `total_revenue >= 0`) against the result.
4. **`plugin_spec`** (`validation/plugin_spec.py`) — structural validation
   against the models in [plugin-format.md](plugin-format.md), including a
   BOM check on every written file.
5. **`cli_validate`** (`validation/cli_validate.py`) — shells out to the
   real `claude plugin validate <dir> --strict`. Warns (doesn't fail) if
   the CLI isn't on `PATH`, since most dev sandboxes won't have it; CI
   always installs it, so this is enforced for real before merge/release.
6. **`mcp_smoke`** (`validation/mcp_smoke.py`) — spawns the bundled
   `mis-mcp-runtime` over stdio with the generated config and calls each
   tool through a real MCP client, asserting non-error responses.
7. **`self_critique`** (`validation/self_critique.py`) — an LLM review pass
   of the generated skill/agent/command prose against the profile and
   guardrails; `error`-severity findings block the run.

There is no longer a dedicated PII scanner. `is_likely_pii` and
`validation/pii.py` were removed; `sql_safety` still rejects any projection
of a `denied_columns` entry, but `denied_columns` is now populated only
from pack-declared role categories (e.g. `free_text`), not a PII heuristic.
The cookbook and schema-model fact-checks run inside `synthesis.py` at
build time rather than as harness checks.

## Data at rest: redaction before packaging (`packaging/redaction.py`)

`sql_safety`/`pii_policy` only gate what a *query* can return — they say
nothing about what's physically sitting in the packaged plugin's `data/`
folder. For a file-based source (CSV/TSV/Excel/JSON/Parquet/SQLite), the
packager re-derives every table's denied columns from `SchemaProfile`
directly — every table the source has, not just the fact table
`resolve_bindings`'s own `denied_columns` is scoped to — and writes a
`SELECT * EXCLUDE (...)` copy of each table into `data/`, in the same
filename/format the runtime expects, instead of copying the original file
byte-for-byte. A denied column (PII, or a role category the pack's
guardrails deny) is dropped from disk, not just from generated SQL.

## Live-database sources: no snapshot, no baked-in credential (`ingestion/postgres.py`)

A live database source (currently Postgres, via DuckDB's `postgres`
extension) never gets copied into the plugin at all — `write_plugin` skips
`data/` entirely for a source with no `original_paths`, and the shipped
runtime reconnects live on every query instead.

The connection string itself never reaches disk:

- `forge_core.ingestion.registry.prepare_source_for_persistence()` is the
  one place a raw connection string is allowed to exist as a Python string
  outside `os.environ` — it stashes it in the current process's
  environment and hands back a `${FORGE_SOURCE_DB_URL}`-style placeholder,
  which is the only thing ever written to a `RunRecord`, the jobs database,
  a log line, or an API response.
- `config/data_source.json`'s `duckdb_attach_sql` stores that same
  placeholder. Both `forge_core.runtime_session.open_session` (generation
  time) and `mis_mcp_runtime.engine.duckdb_session.open_session` (the
  shipped runtime, on the customer's machine) resolve `${VAR}` from their
  own process's environment at connect time — the customer supplies the
  real credential via that env var when they install the plugin, the same
  way `GEMINI_API_KEY` is supplied to this repo's own CLI/API.

## Runtime security (`packages/mcp-runtime/src/mis_mcp_runtime/security/`)

The runtime is generic and config-driven, but it enforces its own
independent layer of the same guarantees the harness checked at
generation time, because it's the thing actually executing queries at
plugin-run time:

- **`sql_policy.py`** — parses every incoming query with `sqlglot`; only
  read-only `SELECT`/`WITH` statements are allowed, ever.
- **`allowlist.py`** — rejects any table not in `config/schema_bindings.json`'s
  `allowed_tables`.
- **`pii_policy.py`** — rejects any query that would project a
  `denied_columns` entry, independent of whether the SQL came from a
  compiled KPI or `run_safe_query`.
- **`limits.py`** — injects a row `LIMIT` and a statement timeout on every
  query so a malformed or adversarial `run_safe_query` call can't exhaust
  resources.

All four modules **fail closed**: malformed or missing configuration
results in a refusal to execute, never a permissive default.
