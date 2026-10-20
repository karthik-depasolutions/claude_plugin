"""The client data warehouse: loads an upload straight into a dedicated,
narrow-access Postgres schema on the shared staging database instead of
saving files to disk, so the rest of the pipeline - and the shipped plugin,
at runtime - can read the client's data live via `PostgresAdapter`
(`forge_core.ingestion.postgres`) instead of bundling a copy of it.

Isolation model, one schema and one role per run:

- `client_<run_id>` schema, owned by the admin role, holding one table per
  uploaded file.
- `client_<run_id>_ro` role: `LOGIN`, a random password, a low
  `CONNECTION LIMIT`, a per-session `statement_timeout`, membership in the
  `forge_client_group` group role (the only thing `pg_hba.conf` on the
  staging server grants remote TLS access to), `USAGE` on its own schema and
  `SELECT` on its own tables only - nothing else, ever.

The admin connection (`admin_connection_string`) is expected to only be
reachable from where this code runs (in practice: over the existing SSH
tunnel to the staging box, or from the same host in production) - `pg_hba.conf`
has no rule granting it remote access, by design. See
`scripts/ops/staging_warehouse_setup.sh` for the server-side half of this.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import duckdb

from forge_core.ingestion.postgres import extract_schema

GROUP_ROLE = "forge_client_group"
CONNECTION_LIMIT = 5
STATEMENT_TIMEOUT = "15s"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9]{1,32}$")
LABEL_MAX_LEN = 20
_LABEL_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")
_WARMUP_ATTEMPTS = 8
_WARMUP_BASE_DELAY_S = 2.0

# Caps on cloning a live source database into the warehouse (see
# provision_client_schema_from_postgres). A source over either limit is
# rejected outright - nothing partial is stored. Overridable per deployment.
MAX_CLONE_TABLES = int(os.environ.get("FORGE_MAX_CLONE_TABLES", os.environ.get("FORGE_CLONE_MAX_TABLES", "200")))
MAX_CLONE_TOTAL_ROWS = int(os.environ.get("FORGE_CLONE_MAX_ROWS", "2000000"))


class CloneTooLargeError(RuntimeError):
    """The source schema exceeds a clone cap. Raised *before* anything is
    created on the warehouse, so there is nothing to roll back."""

_EXTENSION_TABLE_SQL: dict[str, str] = {
    ".csv": "read_csv_auto('{path}')",
    ".tsv": "read_csv_auto('{path}', delim='\\t')",
    ".json": "read_json_auto('{path}')",
    ".ndjson": "read_json_auto('{path}')",
    ".jsonl": "read_json_auto('{path}')",
    ".parquet": "read_parquet('{path}')",
}
_EXCEL_EXTENSIONS = {".xlsx", ".xls"}
SUPPORTED_EXTENSIONS = set(_EXTENSION_TABLE_SQL) | _EXCEL_EXTENSIONS


@dataclass(frozen=True)
class WarehouseCredentials:
    """Everything a caller needs to hand a freshly-provisioned schema's
    connection string to a client, once - never persisted, never logged."""

    connection_string: str
    schema_name: str
    role_name: str


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.match(run_id):
        raise ValueError(
            f"Refusing to provision a schema for run_id {run_id!r}: it must be "
            "lowercase alphanumeric (this becomes a Postgres identifier)."
        )


def _sanitize_label(label: str) -> str:
    """Turns an arbitrary upload label (e.g. a filename stem) into a short,
    lowercase, underscore-separated slug that's safe to splice into a
    Postgres identifier. Never raises - worst case this returns ''."""
    slug = _LABEL_SEPARATOR_PATTERN.sub("_", label.lower()).strip("_")
    return slug[:LABEL_MAX_LEN].strip("_")


def schema_name_for(run_id: str, label: str | None = None) -> str:
    """The one place that decides a run's schema name, so provisioning and
    deprovisioning can never disagree on it. Including a human-readable
    label (when available) alongside the run_id is purely for making the
    schema identifiable in a tool like pgAdmin - the run_id alone already
    guarantees uniqueness."""
    slug = _sanitize_label(label) if label else ""
    return f"client_{slug}_{run_id}" if slug else f"client_{run_id}"


def _resolve_upload_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    files = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not files:
        raise ValueError(f"No supported files found in {source} (looked for {sorted(SUPPORTED_EXTENSIONS)}).")
    return files


def _sanitize_table_name(stem: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in stem.strip().lower()).strip("_") or "table"
    return f"t_{cleaned}" if cleaned[0].isdigit() else cleaned


def _load_file_into_schema(
    con: duckdb.DuckDBPyConnection, catalog: str, schema: str, file_path: Path, table_name: str
) -> None:
    qualified = f'{catalog}."{schema}"."{table_name}"'
    ext = file_path.suffix.lower()
    if ext in _EXCEL_EXTENSIONS:
        import pandas as pd

        view_name = f"_forge_excel_{table_name}"
        con.register(view_name, pd.read_excel(file_path))
        con.execute(f"CREATE TABLE {qualified} AS SELECT * FROM {view_name}")
        con.unregister(view_name)
    else:
        read_sql = _EXTENSION_TABLE_SQL[ext].format(path=file_path.as_posix())
        con.execute(f"CREATE TABLE {qualified} AS SELECT * FROM {read_sql}")


def provision_client_schema(
    admin_connection_string: str,
    run_id: str,
    upload_path: Path,
    *,
    public_host: str,
    public_port: int = 5432,
    database: str = "forge_warehouse",
    catalog: str = "srcdb",
    label: str | None = None,
    public_username_suffix: str | None = None,
) -> WarehouseCredentials:
    """Loads `upload_path` (a single file or a directory of files, same shape
    `FileAdapter` accepts) into a brand-new `client_<run_id>` schema (or
    `client_<label>_<run_id>` when `label` is given - see `schema_name_for`)
    on the warehouse database, creates a narrow read-only role scoped to
    just that schema, and returns the connection string a client plugin
    should use - pointed at `public_host`/`public_port` (the
    network-reachable address), not wherever `admin_connection_string`
    itself resolves to (typically an SSH-tunnelled loopback address, which
    only this process can reach).

    `public_username_suffix`: set this when `public_host` is a connection
    pooler that multiplexes many tenants' Postgres instances behind one
    address and needs the target project encoded in the username - e.g.
    Supabase's pooler, where the role must be given as `<role>.<project_ref>`
    rather than plain `<role>`. Direct (non-pooled) connections leave this
    `None`.
    """
    _validate_run_id(run_id)
    schema_name = schema_name_for(run_id, label)
    role_name = f"{schema_name}_ro"
    password = secrets.token_urlsafe(24)

    files = _resolve_upload_files(upload_path)

    con = duckdb.connect(":memory:")
    con.execute("SET enable_progress_bar = false")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{admin_connection_string}' AS {catalog} (TYPE POSTGRES)")

        con.execute(f"CALL postgres_execute('{catalog}', $ddl$CREATE SCHEMA \"{schema_name}\"$ddl$)")
        for file_path in files:
            table_name = _sanitize_table_name(file_path.stem)
            _load_file_into_schema(con, catalog, schema_name, file_path, table_name)

        _create_scoped_role(con, catalog, schema_name, role_name, password)
    finally:
        con.close()

    public_user = f"{role_name}.{public_username_suffix}" if public_username_suffix else role_name
    _wait_until_role_is_connectable(public_host, public_port, database, public_user, password)
    connection_string = (
        f"postgresql://{quote(public_user)}:{quote(password)}@{public_host}:{public_port}/{database}"
        f"?options=-csearch_path%3D{schema_name}&sslmode=require"
    )
    return WarehouseCredentials(
        connection_string=connection_string, schema_name=schema_name, role_name=role_name
    )


def provision_client_schema_from_postgres(
    admin_connection_string: str,
    run_id: str,
    source_connection_string: str,
    *,
    public_host: str,
    public_port: int = 5432,
    database: str = "forge_warehouse",
    catalog: str = "srcdb",
    source_schema: str | None = None,
    label: str | None = None,
    public_username_suffix: str | None = None,
) -> WarehouseCredentials:
    """Clone one schema of a live source Postgres into a brand-new
    `client_<run_id>` schema (or `client_<label>_<run_id>`) on the warehouse,
    with a narrow read-only role scoped to just that schema - the live-DB
    equivalent of `provision_client_schema` (which does the same for files).

    `source_schema` defaults to whatever the source connection string's
    `search_path` names (via `postgres.extract_schema`), else `'public'` -
    matching what a plain live run would read.

    Raises `CloneTooLargeError`, *before creating anything*, when the source
    schema has more than `MAX_CLONE_TABLES` tables or more than
    `MAX_CLONE_TOTAL_ROWS` rows in total. Every row is streamed through this
    process (DuckDB `CREATE TABLE … AS SELECT`), so the caps also bound how
    much data transits and how long the run's INGEST stage blocks.
    """
    _validate_run_id(run_id)
    schema_name = schema_name_for(run_id, label)
    role_name = f"{schema_name}_ro"
    password = secrets.token_urlsafe(24)
    src_schema = source_schema or extract_schema(source_connection_string)

    con = duckdb.connect(":memory:")
    con.execute("SET enable_progress_bar = false")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        from forge_core.ingestion.postgres import apply_pg_compat  # noqa: PLC0415

        apply_pg_compat(con)
        con.execute(f"ATTACH '{source_connection_string}' AS clonesrc (TYPE POSTGRES, READ_ONLY)")

        table_names = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE database_name = 'clonesrc' "
                "AND schema_name = ? ORDER BY table_name",
                [src_schema],
            ).fetchall()
        ]
        if not table_names:
            raise ValueError(
                f"No tables found in schema '{src_schema}' of the source database - nothing to clone."
            )
        if len(table_names) > MAX_CLONE_TABLES:
            raise CloneTooLargeError(
                f"The source schema '{src_schema}' has {len(table_names)} tables; the clone limit "
                f"is {MAX_CLONE_TABLES}. Use live mode, or point the connection string at a schema "
                "with fewer tables."
            )
        total_rows = 0
        for name in table_names:
            count_row = con.execute(
                f'SELECT COUNT(*) FROM clonesrc."{src_schema}"."{name}"'
            ).fetchone()
            assert count_row is not None
            total_rows += int(count_row[0])
        if total_rows > MAX_CLONE_TOTAL_ROWS:
            raise CloneTooLargeError(
                f"The source schema '{src_schema}' holds ~{total_rows:,} rows; the clone limit is "
                f"{MAX_CLONE_TOTAL_ROWS:,}. Use live mode instead."
            )

        con.execute(f"ATTACH '{admin_connection_string}' AS {catalog} (TYPE POSTGRES)")
        con.execute(f'CALL postgres_execute(\'{catalog}\', $ddl$CREATE SCHEMA "{schema_name}"$ddl$)')
        for name in table_names:
            con.execute(
                f'CREATE TABLE {catalog}."{schema_name}"."{name}" AS '
                f'SELECT * FROM clonesrc."{src_schema}"."{name}"'
            )
        _create_scoped_role(con, catalog, schema_name, role_name, password)
    finally:
        con.close()

    public_user = f"{role_name}.{public_username_suffix}" if public_username_suffix else role_name
    _wait_until_role_is_connectable(public_host, public_port, database, public_user, password)
    connection_string = (
        f"postgresql://{quote(public_user)}:{quote(password)}@{public_host}:{public_port}/{database}"
        f"?options=-csearch_path%3D{schema_name}&sslmode=require"
    )
    return WarehouseCredentials(
        connection_string=connection_string, schema_name=schema_name, role_name=role_name
    )


def _wait_until_role_is_connectable(
    public_host: str, public_port: int, database: str, public_user: str, password: str
) -> None:
    """A pooling proxy in front of Postgres (e.g. Supabase's Supavisor) can
    cache role credentials and reject a role for several seconds right after
    it's created - a well-documented race (see supabase/supavisor#728) that
    otherwise shows up as an intermittent, hard-to-reproduce auth failure the
    *first* time a freshly-provisioned run tries to read its own data back.
    Retrying here, before `provision_client_schema` ever hands out the
    connection string, means callers only ever see credentials that already
    work. Near-instant (one attempt) against a direct, non-pooled Postgres.

    Raises `RuntimeError` if the role is still unreachable after the last
    retry: a connection string that never verified here is one the shipped
    plugin can't use either, and both provisioning callers already roll the
    partial schema back on any exception. Failing the run now beats packaging
    a plugin whose every query dies with an auth error - which is exactly
    what "give up silently" produced against a pooler mid-propagation."""
    probe_url = f"postgresql://{quote(public_user)}:{quote(password)}@{public_host}:{public_port}/{database}?sslmode=require"
    last_error: Exception | None = None
    for attempt in range(_WARMUP_ATTEMPTS):
        con = duckdb.connect(":memory:")
        con.execute("SET enable_progress_bar = false")
        try:
            con.execute("INSTALL postgres; LOAD postgres;")
            con.execute(f"ATTACH '{probe_url}' AS warmup (TYPE POSTGRES, READ_ONLY)")
            return
        except Exception as exc:  # noqa: BLE001 - any failure just means "not ready yet"
            last_error = exc
            if attempt < _WARMUP_ATTEMPTS - 1:
                time.sleep(_WARMUP_BASE_DELAY_S * (attempt + 1))
        finally:
            con.close()
    raise RuntimeError(
        f"warehouse role {public_user!r} was not connectable after {_WARMUP_ATTEMPTS} attempts; "
        "the connection pooler may still be propagating it, or repeated auth failures tripped a "
        f"circuit breaker. Last error: {last_error}"
    ) from last_error


def _create_scoped_role(
    con: duckdb.DuckDBPyConnection, catalog: str, schema_name: str, role_name: str, password: str
) -> None:
    # Every value here is server-generated (run_id already validated as
    # [a-z0-9]+, password is url-safe base64) - no untrusted input reaches
    # this SQL, but dollar-quoting keeps it safe even so.
    statements = [
        f"CREATE ROLE \"{role_name}\" LOGIN PASSWORD '{password}' CONNECTION LIMIT {CONNECTION_LIMIT}",
        f"ALTER ROLE \"{role_name}\" SET statement_timeout = '{STATEMENT_TIMEOUT}'",
        f"GRANT {GROUP_ROLE} TO \"{role_name}\"",
        f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role_name}"',
        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema_name}" TO "{role_name}"',
        # So a table added to this schema later (re-upload / append) is
        # readable by the same role without re-granting by hand.
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema_name}" GRANT SELECT ON TABLES TO "{role_name}"',
    ]
    for statement in statements:
        con.execute(f"CALL postgres_execute('{catalog}', $ddl${statement}$ddl$)")


def deprovision_client_schema(
    admin_connection_string: str,
    run_id: str,
    *,
    catalog: str = "srcdb",
    label: str | None = None,
    schema_name: str | None = None,
) -> None:
    """Symmetric teardown: drops the schema (with everything in it) and the
    role. Safe to call even if provisioning only got partway through.

    Pass `schema_name` when it's known exactly (e.g. persisted on the run
    row) - it's used verbatim. Otherwise it's recomputed from `run_id` +
    `label`, which must match what `provision_*` was given or this safely
    no-ops on the wrong name."""
    _validate_run_id(run_id)
    if schema_name is None:
        schema_name = schema_name_for(run_id, label)
    role_name = f"{schema_name}_ro"

    con = duckdb.connect(":memory:")
    con.execute("SET enable_progress_bar = false")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{admin_connection_string}' AS {catalog} (TYPE POSTGRES)")
        con.execute(
            f"CALL postgres_execute('{catalog}', "
            f"$ddl$DROP SCHEMA IF EXISTS \"{schema_name}\" CASCADE$ddl$)"
        )
        con.execute(f"CALL postgres_execute('{catalog}', $ddl$DROP ROLE IF EXISTS \"{role_name}\"$ddl$)")
    finally:
        con.close()


__all__ = [
    "CONNECTION_LIMIT",
    "GROUP_ROLE",
    "MAX_CLONE_TABLES",
    "MAX_CLONE_TOTAL_ROWS",
    "STATEMENT_TIMEOUT",
    "SUPPORTED_EXTENSIONS",
    "CloneTooLargeError",
    "WarehouseCredentials",
    "deprovision_client_schema",
    "provision_client_schema",
    "provision_client_schema_from_postgres",
    "schema_name_for",
]
