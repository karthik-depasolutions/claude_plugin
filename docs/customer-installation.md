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
/plugin marketplace add <owner>/<repo>
/plugin install <plugin-name>@<repo>
/reload-plugins
```

Use this for a plugin published with `forge_core.publishing.standalone_repo`
— either via the web wizard's "Publish to GitHub" button (shown once a run
succeeds), `POST /runs/{run_id}/publish/github`, or `forge publish github
<plugin_dir>` from the CLI. Each of these creates a brand-new, self-
installable repo (see [marketplace.md](marketplace.md#one-click-publish-standalone-repo))
and hands back the exact two commands above with the real owner/repo/plugin
names filled in — the repo's own `README.md` has them too. One repo per
customer this way is useful when a customer's schema (and therefore their
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
snapshot inside the plugin at all. Before first use, export the credential
under the env var name `config/data_source.json` names (`FORGE_SOURCE_DB_URL`,
by default):

```bash
export FORGE_SOURCE_DB_URL="postgresql://user:password@host:5432/dbname"
```

Without it, the bundled runtime fails closed with a clear error naming the
missing variable — it never falls back to any default or cached data.

There are two ways this connection string comes about:

- **Customer's own database** — you supply the credential yourself; it's the
  same one the generator connected with while profiling your schema.
- **Client data warehouse** (uploads made through the web wizard's "upload
  files" flow, when the API is configured with `FORGE_CLIENT_WAREHOUSE_URL`) —
  your uploaded files were loaded into a dedicated, isolated Postgres schema
  that only your plugin's narrow, read-only credential can reach. That
  credential is shown to you **exactly once**, right after your run
  succeeds, on the web wizard's success screen — it is never written to any
  file, log, or repo, including this plugin's own. If you lose it, there is
  no way to recover it; ask whoever operates the generator to re-run your
  upload. See `packages/forge-core/src/forge_core/ingestion/warehouse.py`
  for exactly how that schema and credential are provisioned.

## Verifying an install is healthy

```bash
claude plugin validate <plugin-dir> --strict
```

This is the exact command the [validation harness](security.md)'s
`cli_validate` check runs during generation — if it passed then, it will
pass on the machine the plugin is installed on, since the plugin directory
doesn't change between packaging and installation.
