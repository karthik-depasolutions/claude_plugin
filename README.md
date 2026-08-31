# MIS Plugin Forge

**A system that generates Claude Code / Claude Desktop plugins from a customer's MIS data source — not a single plugin.**

```text
Data Source (CSV / Excel / JSON / Parquet / SQLite / Postgres*)
        ↓
   Profile (deterministic + LLM)
        ↓
   Industry Detection (pack matching)
        ↓
   Schema Binding (canonical role → physical column)
        ↓
   Generate (skills, agents, commands, hooks, KPI defs, artifacts)
        ↓
   Validate (8-check trust boundary)
        ↓
   Installable, versioned Claude Plugin
```

*Files, SQLite, and a live read-only Postgres connection are supported today; MySQL/Snowflake/BigQuery adapters are planned (see [docs/architecture.md](docs/architecture.md)).*

## Why this is a generator, not a plugin

The critical design decision, straight from [docs/source-architecture-doc.md](docs/source-architecture-doc.md):

> Generate the plugin's customer-specific configuration and reasoning components; keep the MCP execution infrastructure generic, controlled, and reusable.

Concretely:

- **Industry packs** (`industry-packs/*/kpis/*.json`) define KPIs once, in terms of **canonical roles** (`revenue_amount`, `transaction_status`), not any one customer's column names.
- The **binding resolver** (`packages/forge-core/src/forge_core/binding/`) maps a specific customer's real columns onto those canonical roles and writes `config/schema_bindings.json`.
- The **KPI compiler** (`packages/forge-core/src/forge_core/compiler/`) joins pack KPI + bindings into concrete, `sqlglot`-validated SQL in `config/kpi_defs.json`. The LLM never writes executable code that ships to a customer — only proposals that get compiled and verified.
- The **generic MCP runtime** (`packages/mcp-runtime/`) is one implementation that executes `config/*.json` for every customer. It is versioned and shipped independently of any single generated plugin.
- The **understanding phase is mandatory**: after deterministic profiling (structure, relationships, grain, value sets, statistical patterns) an LLM pass synthesizes `config/schema_model.json` — the knowledge pack the plugin ships (per-table docs, enum decodes, pattern notes, a dry-run-verified query cookbook). The MCP runtime serves it to any client as `schema://` resources and folds its overview + caveats into the server `instructions`.
- Everything generated must pass the **validation harness** (`packages/forge-core/src/forge_core/validation/harness.py`) before packaging — schema fact-check, SQL safety, DuckDB dry-run, plugin-spec validation, the real `claude plugin validate --strict`, an MCP stdio smoke test, and an LLM self-critique pass (7 checks).

## Repository layout

```text
packages/forge-core/      # the generator engine (ingest → profile → classify → bind → generate → validate → package → publish)
packages/mcp-runtime/     # the one generic MCP server every generated plugin ships or points at
apps/api/                 # FastAPI service that runs the generator as async jobs
apps/web/                 # wizard UI: connect source → review profile → confirm industry → review bindings → generate → validate → download/publish
industry-packs/           # the versioned knowledge base: entities, KPIs, skill/agent/recipe templates, guardrails per industry
plugin-templates/base/    # skeleton a generated plugin starts from
fixtures/                 # sample datasets, golden verified-valid plugins, JSON Schemas, LLM cassettes
generated/                # gitignored — pipeline run output lives here
marketplace/              # publishable marketplace catalog (.claude-plugin/marketplace.json + plugins/)
legacy/                   # the original single-customer POC pipeline, kept for reference during reimplementation
docs/                     # architecture, generator flow, plugin format, security, marketplace, install docs
```

See [docs/architecture.md](docs/architecture.md) for the full data flow and [docs/plugin-format.md](docs/plugin-format.md) for the exact Claude plugin spec constraints the packager enforces.

## Quick start (local dev)

```bash
uv sync --all-packages --dev
cp .env.example .env   # GEMINI_API_KEY is required — the understanding phase is not optional

# Generate a plugin from the bundled sample dataset (the full pipeline,
# including the 7-check validation harness, runs as part of `forge run`)
uv run forge run fixtures/datasets/bookings.csv --out generated/demo

# Re-check just the plugin-spec structural rules against packaged output
uv run forge validate generated/demo/<pack-slug>-mis-plugin

# Try it in Claude Code
claude --plugin-dir generated/demo/<pack-slug>-mis-plugin
```

Run the API (job orchestration, SSE progress, downloads) instead of the CLI:

```bash
uv run --package forge-api uvicorn forge_api.main:app --reload --port 8420
# API:  http://localhost:8420/docs

curl -X POST localhost:8420/runs -H 'content-type: application/json' \
  -d '{"source_path": "fixtures/datasets/bookings.csv"}'
```

Run the web wizard against that API without Docker:

```bash
cd apps/web && npm install && npm run dev
# Web:  http://localhost:5420  (proxies /runs, /packs, /health to :8420 in dev)
```

Or start both at once:

```bash
./scripts/dev-up.ps1   # Windows/PowerShell
```

Or the whole stack (Postgres + API + web UI) via Docker:

```bash
docker compose up --build
# API:  http://localhost:8420/docs
# Web:  http://localhost:5420
```

Ports are deliberately 8420/5420, not the more common 8000/5173 — on a dev
box already running other projects' containers, a collision on the common
ports fails *silently* (uvicorn/vite just bind to, or proxy to, whatever
else already owns that port, and every API call 404s against the wrong
app) rather than with a clear error.

## Status

Actively being rebuilt from a working single-customer POC (see `legacy/`) into the generic platform described above. Track milestones in the project plan.
