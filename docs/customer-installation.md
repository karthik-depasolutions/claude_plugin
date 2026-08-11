# Installing a generated plugin

Every plugin this platform generates is a standard Claude Code plugin —
nothing about installing it is Forge-specific. Three ways to get one, from
least to most involved:

## 1. From a local directory (fastest, e.g. right after `forge run`)

```bash
claude --plugin-dir generated/demo/<pack-slug>-mis-plugin
```

Loads the plugin for that session only — no install step, useful for
trying a freshly generated plugin immediately.

## 2. From a marketplace

```text
/plugin marketplace add <org>/<marketplace-repo>
/plugin install <plugin-name>@<marketplace-name>
/reload-plugins
```

Use this for anything published via `forge publish marketplace` (see
[marketplace.md](marketplace.md)) — the standard path for distributing a
plugin to a team or customer who isn't the one running the generator.

## 3. From a customer-specific GitHub repo

```text
/plugin marketplace add <org>/<customer-repo>
/plugin install <plugin-name>@<customer-repo>
```

Use this for a plugin pushed via `forge_core.publishing.github` — one repo
per customer, useful when a customer's schema (and therefore their
generated plugin) is updated on its own release cadence independent of the
shared marketplace.

## What happens on first use

The bundled `mcp_server/` (a copy of `mis-mcp-runtime`, see
[architecture.md](architecture.md)) starts over stdio the first time the
plugin's skill or agent is invoked. It reads `config/schema_bindings.json`
and `config/kpi_defs.json` from the plugin's own directory
(`${CLAUDE_PLUGIN_ROOT}`) — no network calls, no external service, no
customer data leaves the machine Claude Code is running on. If its Python
dependencies (`mcp_server/requirements.txt`) aren't already installed,
install them once:

```bash
pip install -r <plugin-dir>/mcp_server/requirements.txt
```

## Plugins generated from a live database

If the plugin was generated from a live database connection (Postgres, for
now) rather than uploaded files, `mcp_server/` connects live on every query
instead of reading from a bundled `data/` folder — there is no data
snapshot inside the plugin at all. Before first use, export the same
credential the generator used, under the env var name
`config/data_source.json` names (`FORGE_SOURCE_DB_URL`, by default):

```bash
export FORGE_SOURCE_DB_URL="postgresql://user:password@host:5432/dbname"
```

Without it, the bundled runtime fails closed with a clear error naming the
missing variable — it never falls back to any default or cached data.

## Verifying an install is healthy

```bash
claude plugin validate <plugin-dir> --strict
```

This is the exact command the [validation harness](security.md)'s
`cli_validate` check runs during generation — if it passed then, it will
pass on the machine the plugin is installed on, since the plugin directory
doesn't change between packaging and installation.
