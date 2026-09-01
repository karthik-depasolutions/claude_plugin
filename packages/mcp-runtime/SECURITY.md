# MCP runtime — security model

`mis-mcp-runtime` is the one generic server every generated plugin ships. It
speaks **stdio to a single local MCP client** (Claude Desktop, Claude Code,
Cursor). There is no network listener, no auth token, no pairing step, and no
multi-tenant state. It holds one database connection, described by
`config/data_source.json`, and it is **read-only**.

That threat model is much narrower than a hosted database gateway's, so the
discipline here is about *what a compromised or confused model can make the
server do or reveal*, not about authenticating callers.

## Guarantees

Every guarantee below is enforced structurally (a parse, not a keyword
regex) and **fails closed**.

| Guarantee | Where |
|---|---|
| Only `SELECT` / `WITH … SELECT` reaches the database. `INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/ATTACH/COPY/PRAGMA/MERGE/TRUNCATE` and multi-statement input are rejected before execution. | `security/sql_policy.py` |
| A query may reference only the tables in `schema_bindings.json`'s `allowed_tables`. An empty allow-list rejects everything (and `config.py` refuses to start with one). | `security/allowlist.py` |
| `SELECT *` is rejected, so every projected column is explicit and checkable. Columns in `denied_columns` cannot be selected, referenced, or filtered on. | `security/sql_policy.py`, `security/pii_policy.py` |
| Row count and wall-clock time are capped (`max_query_rows`, `query_timeout_seconds` from the industry pack). A missing or larger `LIMIT` is rewritten; a slow query is interrupted. | `security/limits.py` |
| Only pre-compiled, pre-validated SQL from `kpi_defs.json` runs for KPI/metric tools. `run_safe_query` is the single tool that accepts free SQL text, and it runs the full guard chain above. | `tools/*`, `security/*` |
| No tool raises into the session, and no error message returns a connection string, a filesystem path, or an internal physical table name. Errors are a fixed set of codes: `invalid_argument`, `not_found`, `denied`, `timeout`, `query_failed`, `internal_error`. | `errors.py` (`tool_guard`) |
| Every tool call emits one line to stderr — the MCP host's log — with the tool name, outcome, duration, row count, and error code. Argument *values* are never logged (a filter can hold PII). | `errors.py` (`_audit`) |
| Every tool declares MCP annotations: `read_only_hint=true`, `destructive_hint=false`, plus `open_world_hint` = whether it reaches the source DB. | `server.py` (`_META` / `_DATA`) |

The generator side re-checks the SQL surface independently before packaging —
`forge_core.validation.sql_safety` and the `sql_safety` / `dry_run` harness
checks — so a bad KPI never ships.

## Rule for changes here

Widening any limit — the allow-list, `denied_columns`, `max_query_rows`, the
statement types `sql_policy` permits, the fields a tool exposes — **is the
change**, not a side effect of one. Say so in the commit message and adjust
the tests that pin the current bound.

## Deliberately not implemented

These belong to a hosted, multi-client, writable gateway (e.g. TablePro's MCP
server). They add no safety to a read-only local stdio plugin and are left
out on purpose:

- Bearer tokens, scopes, per-connection allow-lists, pairing / handshake files
- Rate limiting
- Elicitation and a separate `confirm_destructive_operation` tool — nothing
  here is destructive
- A hash-chained (tamper-evident) audit log — the log sink is the user's own
  machine; a plain line is enough. Revisit only if these plugins are ever run
  where the log itself is untrusted.
