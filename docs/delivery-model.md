# Delivery model

How a generated plugin reaches the customer's Claude Code / Desktop, and
where its MCP runtime executes. There are two patterns. **Pattern A is the
default and the only one a plain `forge run` / `POST /runs` produces.**

## Pattern A — bundled stdio runtime (default)

`write_plugin` (`forge_core.packaging.plugin_builder`) always emits a plugin
whose `.mcp.json` is a **stdio** server:

```json
{ "mcpServers": { "mis-mcp-<slug>": {
  "command": "python",
  "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_server/run_server.py"],
  "env": { "MIS_MCP_CONFIG_DIR": "${CLAUDE_PLUGIN_ROOT}/config",
           "MIS_MCP_DATA_DIR": "${CLAUDE_PLUGIN_ROOT}/data" } }}}
```

- The server key is **per-plugin** — `mis-mcp-<pack-slug>`, or
  `mis-mcp-<label-slug>` when a customer label is given (`server_name_for`) —
  so two Forge plugins installed side by side don't both register as
  `mis-mcp-runtime` and collide in Claude Desktop's connector list. The
  generated `tools:` refs in agents/commands/skills are repointed to match.
  `bind_http_mcp` preserves whatever key the stdio plugin already had.
- The whole `mis-mcp-runtime` package is vendored into `mcp_server/`
  (`packaging/mcp_bundle.py`) with a stdlib-only `run_server.py` and a
  `requirements.txt`.
- For a file-based source, the redacted `data/` files ship with the plugin.
  For a live-DB source, `.mcp.json` also carries
  `FORGE_SOURCE_DB_URL=${user_config.source_db_url}` and Claude Desktop
  prompts for the connection string on Connect — the credential is never
  baked in.
- Claude Desktop / Code spawns `run_server.py` as a child process and talks
  to it over stdin/stdout. No network listener, no shared service, nothing
  leaves the machine (except the live-DB connection the customer configures).
- The customer runs `pip install -r mcp_server/requirements.txt` once.

Every distribution channel ships Pattern A by default: `forge publish local`,
`forge publish marketplace`, `forge publish github`, and the web wizard's
Download button.

## Pattern B — hosted Streamable-HTTP runtime (advanced, opt-in)

`bind_http_mcp` (`plugin_builder.py`) **rewrites** an already-packaged plugin
from stdio to a remote HTTP server:

```json
{ "mcpServers": { "mis-mcp-runtime": {
  "type": "http",
  "url": "https://<FORGE_PUBLIC_BASE_URL>/mcp/<run_id>/<token>" }}}
```

- It also strips `source_db_url` from `plugin.json` `userConfig` and rewrites
  the README so the customer needs no local Python.
- The runtime is served by the Forge API itself: `HostedMCPGateway` mounted
  at `/mcp` in `apps/api/src/forge_api/main.py`, backed by
  `apps/api/src/forge_api/hosted_mcp.py`. Auth is the per-plugin
  `.mcp_token` (`ensure_mcp_token`), compared with `secrets.compare_digest`.
  The API loads that run's `config/` + `data/` from `FORGE_RUNS_DIR` on
  demand.
- Because the runtime runs *inside the API*, not on a customer machine, a
  live-DB run can't get its credential from a customer env var. Each
  successful API run therefore persists its resolved source connection
  string to `<FORGE_RUNS_DIR>/<run_id>/.mcp_source_env.json` (server-side
  only — never shipped; see `docs/security.md`), and the gateway feeds it to
  `open_session` per run. Without this a hosted run falls back to the API's
  single global `FORGE_SOURCE_DB_URL` and reaches the wrong database.
- **`bind_http_mcp` runs only inside `POST /runs/{run_id}/publish/github`,
  and only when `FORGE_PUBLIC_BASE_URL` is set.** Without it, that route 400s
  with an explanatory message and no plugin is published. `forge run`, the
  CLI, and every other route never call it.
- Use this when: the audience cannot run local Python, or the plugin is
  installed from GitHub on machines you do not control. The trade-off: you
  operate a long-lived API with network reach to the customer's data, and
  every plugin's traffic flows through it.

`FORGE_PUBLIC_BASE_URL` is a public HTTPS origin that reaches the Forge API.
In production that is a real domain behind Cloudflare Tunnel or similar; for
local demos a reserved `ngrok` domain works (`bind_http_mcp` adds the
`ngrok-skip-browser-warning` header automatically for `*.ngrok*` URLs).

## Source posture (orthogonal to Pattern A/B)

Where the plugin's data lives, decided at run time:

| Posture | How | Plugin ships | Customer DB touched |
|---|---|---|---|
| **Bundled files** | a file/upload source (default) | redacted `data/` copies | never |
| **Live database** | a `postgresql://` source (default for DB sources) | a `${FORGE_SOURCE_DB_URL}` contract only | on every query, read-only |
| **Cloned database** | a `postgresql://` source with `clone: true` | a `${FORGE_SOURCE_DB_URL}` contract pointed at a Forge-warehouse copy | only during the one-time clone |

"Cloned" needs the client warehouse configured and is capped
(`FORGE_CLONE_MAX_TABLES` / `FORGE_CLONE_MAX_ROWS`); see
[client-warehouse.md](client-warehouse.md). It exists for sources that
can't be reached at install time, or when the customer would rather Forge
hold a snapshot than have the plugin hit production.

## Which command produces which

| Command / route | Delivery | Notes |
|---|---|---|
| `forge run` | Pattern A | Always. |
| `POST /runs`, `POST /runs/upload` | Pattern A | Always. |
| `forge publish local` / `github` / `marketplace` | Pattern A | Ships the stdio plugin as-is. |
| Web wizard "Download" | Pattern A | Zips whatever is on disk. |
| `POST /runs/{id}/publish/github` **with `FORGE_PUBLIC_BASE_URL` set** | **Pattern B** | Rewrites `.mcp.json` to the hosted URL before pushing the repo. |
| Web wizard "Publish to GitHub" | A or B | Same as the route above — B iff `FORGE_PUBLIC_BASE_URL` is set. |

## Related

- Live-database sources and the optional client warehouse:
  [client-warehouse.md](client-warehouse.md).
- Install steps per channel: [customer-installation.md](customer-installation.md).
