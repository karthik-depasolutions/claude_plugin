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

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import duckdb

GROUP_ROLE = "forge_client_group"
CONNECTION_LIMIT = 5
STATEMENT_TIMEOUT = "15s"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9]{1,32}$")
LABEL_MAX_LEN = 20
_LABEL_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")

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
    """
    _validate_run_id(run_id)
    schema_name = schema_name_for(run_id, label)
    role_name = f"{schema_name}_ro"
    password = secrets.token_urlsafe(24)

    files = _resolve_upload_files(upload_path)

    con = duckdb.connect(":memory:")
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

    connection_string = (
        f"postgresql://{quote(role_name)}:{quote(password)}@{public_host}:{public_port}/{database}"
        f"?options=-csearch_path%3D{schema_name}&sslmode=require"
    )
    return WarehouseCredentials(
        connection_string=connection_string, schema_name=schema_name, role_name=role_name
    )


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
    admin_connection_string: str, run_id: str, *, catalog: str = "srcdb", label: str | None = None
) -> None:
    """Symmetric teardown: drops the schema (with everything in it) and the
    role. Safe to call even if provisioning only got partway through.
    `label` must match whatever was passed to `provision_client_schema` -
    otherwise this looks for (and safely no-ops on) the wrong schema name."""
    _validate_run_id(run_id)
    schema_name = schema_name_for(run_id, label)
    role_name = f"{schema_name}_ro"

    con = duckdb.connect(":memory:")
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
    "STATEMENT_TIMEOUT",
    "SUPPORTED_EXTENSIONS",
    "WarehouseCredentials",
    "deprovision_client_schema",
    "provision_client_schema",
    "schema_name_for",
]
