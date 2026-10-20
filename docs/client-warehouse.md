# Client data warehouse

An optional home for a run's data on Forge's own infrastructure. Two entry
points:

- **Upload flow** — an uploaded dataset is loaded into a dedicated Postgres
  schema instead of being stored as local files + bundled redacted copies
  (see [delivery-model.md](delivery-model.md), Pattern A).
- **Clone a source database** — when a `postgresql://` source is submitted
  with `clone: true`, one schema of the customer's live database is copied
  into a dedicated warehouse schema, and the plugin is built against (and
  ships pointed at) that copy rather than the customer's database. This is
  the opt-in alternative to the default "query the customer's DB live, copy
  nothing" behaviour.

Both produce the same thing: an isolated `client_*` schema + a scoped
read-only role, and a `${FORGE_SOURCE_DB_URL}` placeholder on the run record.

Off by default. Enabled only when **both** `FORGE_CLIENT_WAREHOUSE_URL` and
`FORGE_CLIENT_WAREHOUSE_PUBLIC_HOST` are set (`Settings.client_warehouse_enabled`,
`apps/api/src/forge_api/config.py`). `GET /health` reports `client_warehouse`
so the web wizard knows whether to offer the clone option.

## What a run provisions

`forge_core.ingestion.warehouse.provision_client_schema`, called from
`POST /runs/upload` (`routers/runs.py::_provision_and_get_source`):

- **One schema per run**: `client_<label-slug>_<run_id>` (or `client_<run_id>`).
  `run_id` is validated against `^[a-z0-9]{1,32}$` before it becomes an
  identifier; the label is lowercased, non-alphanumerics collapsed to `_`,
  truncated to 20 chars.
- Each uploaded file becomes one table in that schema
  (`read_csv_auto` / `read_json_auto` / `read_parquet` / `pandas` for Excel).
- **One dedicated login role per run**: `<schema>_ro`, with a
  `secrets.token_urlsafe(24)` password, `CONNECTION LIMIT 5`,
  `statement_timeout = 15s`, and `GRANT USAGE` + `GRANT SELECT` on **that
  schema only** (plus `ALTER DEFAULT PRIVILEGES` for future tables). It is a
  member of `forge_client_group` — the only role `pg_hba.conf` grants remote
  TLS access to.
- The connection string handed back points at the **public** host
  (`FORGE_CLIENT_WAREHOUSE_PUBLIC_HOST` / `_PORT` / `_DATABASE`), with
  `search_path` pinned to the schema and `sslmode=require`. The admin URL
  (`FORGE_CLIENT_WAREHOUSE_URL`) never leaves the API process.

`deprovision_client_schema` (`DROP SCHEMA ... CASCADE` + `DROP ROLE`) is
idempotent and runs on any provisioning failure.

## Cloning a source database

`provision_client_schema_from_postgres`, called from
`POST /runs` (`routers/runs.py::_provision_clone_and_get_source`) when
`clone: true`:

- One in-memory DuckDB `ATTACH`es the customer's source `READ_ONLY` and the
  warehouse admin read-write, then runs
  `CREATE TABLE <warehouse>.<client_schema>.<t> AS SELECT * FROM <source>.<schema>.<t>`
  per table. Every row streams through the API process.
- **One schema only** — whichever the source connection string's
  `search_path` names (via `postgres.extract_schema`), else `public`. Cloning
  every schema of a database is not supported; point the connection string at
  the schema you want.
- **Size caps, checked before anything is created**: more than
  `FORGE_CLONE_MAX_TABLES` (default 25) tables or `FORGE_CLONE_MAX_ROWS`
  (default 2,000,000) total rows raises `CloneTooLargeError` → the API
  responds `413` and nothing is provisioned. There is no partial or sampled
  clone; the alternative is live mode.
- The scoped role + connection-string shape are identical to the upload path,
  so `PostgresAdapter` consumes the result unchanged.

Unlike the live path, the customer's database is touched **only during the
clone** (read-only). The shipped plugin never connects to it — it reads the
warehouse copy, which is a point-in-time snapshot and does not update.

## Schema lifecycle

`RunORM.warehouse_schema` (migration `0005`) records the `client_*` name for
every warehouse-backed run; `warehouse_provisioned_at` records when.

- A **successful** run's schema is kept — the shipped plugin queries it. It
  is reclaimed only by `DELETE /runs/{run_id}`, which drops the schema + role
  and deletes the run.
- A **failed / cancelled** run's schema is dead weight.
  `scripts/ops/warehouse_gc.py --days N [--apply]` drops those once the run
  has been idle longer than the cutoff. Run it as a daily cron.

## Isolation guarantees

- A run's `_ro` role can read only its own schema. It has no privilege on
  any other `client_*` schema, so a `SELECT` against another run's schema
  fails with a permission error. This is what Guiding principle #6 ("one
  client, one plugin, one dataset") rests on for the upload path.
- The credential is **shown once**, from process memory only, via
  `GET /runs/{run_id}/warehouse-credentials`. It is never written to the
  `runs` table, a log line, or any persisted JSON — `RunRecord` only ever
  carries the `${FORGE_SOURCE_DB_URL}` placeholder
  (`prepare_source_for_persistence`). After an API restart the endpoint
  returns 404 by design.
- Covered by `packages/forge-core/tests/test_warehouse.py` (provisioning,
  scoped-read, cross-schema denial — the last requires a live Postgres via
  `FORGE_TEST_WAREHOUSE_ADMIN_URL`) and `test_warehouse_e2e.py` (a real
  packaged plugin has no `data/` folder and no literal credential in
  `config/data_source.json`).

## Server-side setup (one time)

The Postgres server itself, the `forge_client_group` role, and the
`pg_hba.conf` rule that restricts remote TLS access to that group are **not**
provisioned by Forge. See `scripts/ops/staging_warehouse_setup.sh`. The API
host reaches the admin port over an SSH tunnel, e.g.:

```bash
ssh -i id_e2estaging -N -L 15432:127.0.0.1:5432 root@<host>
# then: FORGE_CLIENT_WAREHOUSE_URL=postgresql://forge_admin:<pw>@127.0.0.1:15432/forge_warehouse
```

`FORGE_CLIENT_WAREHOUSE_PUBLIC_HOST` is the network-reachable host baked into
the string handed to clients — almost always different from the admin URL's
host/port. For a pooled Postgres (e.g. Supabase Supavisor) that needs
`<role>.<project_ref>` usernames, set
`FORGE_CLIENT_WAREHOUSE_PUBLIC_USERNAME_SUFFIX`.
