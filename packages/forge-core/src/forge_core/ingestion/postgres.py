"""Live Postgres ingestion via DuckDB's `postgres` extension. Mirrors
`sqlite.py`'s pattern exactly - `ATTACH ... (TYPE POSTGRES, READ_ONLY)` -
so DuckDB stays the one uniform query surface regardless of source kind.

Unlike every other adapter, this one's `supports`/`ingest` take a
connection-string *string*, not a filesystem `Path` — see
`forge_core.ingestion.registry` for how a source is routed to this adapter
based on its URL scheme instead of `Path.exists()`.

Credential handling: the raw connection string is only ever used in-process,
to introspect the schema right now. It is never written to disk. The
`ConnectionContract` this adapter returns instead references the
connection string as `${FORGE_SOURCE_DB_URL}` (an env var name), which:

- `open_session` (`forge_core.runtime_session`, and its independent runtime
  copy `mis_mcp_runtime.engine.duckdb_session`) resolves from the process
  environment at connect time;
- `ingest()` sets for the rest of *this* generator run, so profiling/
  dry-run/dashboard generation reconnect exactly the way the shipped plugin
  will reconnect on the customer's machine, later, from their own
  environment.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import duckdb

from forge_core.ingestion.base import describe_relation, now_iso, stable_source_id
from forge_core.ingestion.credentials import remember
from forge_core.models.common import SourceKind
from forge_core.models.datasource import ConnectionContract, DataSource

_logger = logging.getLogger(__name__)

CATALOG_ALIAS = "srcdb"
CREDENTIAL_ENV_VAR = "FORGE_SOURCE_DB_URL"
DEFAULT_SCHEMA = "public"
# Accepts the SQLAlchemy-style driver suffix people copy from an app config
# (`postgresql+asyncpg://`, `postgres+psycopg://`, ...) - normalize_pg_url
# strips it before the string reaches libpq (DuckDB's postgres extension).
URL_SCHEME_PATTERN = re.compile(r"^postgres(ql)?(\+[a-z0-9_]+)?://", re.IGNORECASE)
SEARCH_PATH_PATTERN = re.compile(r"-c\s*search_path=([^,\s'\"]+)", re.IGNORECASE)
_DRIVER_SUFFIX_PATTERN = re.compile(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", re.IGNORECASE)
# asyncpg/SQLAlchemy spell TLS `ssl=...`; libpq wants `sslmode=...`.
_SSL_TRUE = {"require", "true", "1", "yes", "on", "allow", "prefer"}
_SSL_FALSE = {"disable", "false", "0", "no", "off"}


def normalize_pg_url(connection_string: str) -> str:
    """Make a Postgres URL libpq-compatible: drop any `+driver` scheme suffix
    and translate an asyncpg-style `ssl=` query param to `sslmode=`. A string
    that is already clean is returned unchanged."""
    s = _DRIVER_SUFFIX_PATTERN.sub(r"\1://", connection_string.strip())
    parsed = urlparse(s)
    if not parsed.query:
        return s
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    out: list[tuple[str, str]] = []
    changed = False
    for key, value in pairs:
        if key.lower() == "ssl" and not any(k.lower() == "sslmode" for k, _ in pairs):
            v = value.strip().lower()
            mode = "require" if v in _SSL_TRUE else "disable" if v in _SSL_FALSE else value
            out.append(("sslmode", mode))
            changed = True
        else:
            out.append((key, value))
    if not changed and s == connection_string:
        return connection_string
    return urlunparse(parsed._replace(query=urlencode(out)))


_ATTACH_RETRY_ATTEMPTS = 5
_ATTACH_RETRY_BASE_DELAY_S = 3.0

# DuckDB's Postgres scanner defaults to a BINARY COPY that hard-fails the whole
# scan when it meets a value it can't decode into the type the catalog
# advertised - real-world analytics exports routinely have jsonb columns
# reported as `numeric`, mixed-dimension arrays, and domain types. The TEXT
# protocol decodes anything Postgres can print; it is the safe default here.
# Set FORGE_PG_TEXT_PROTOCOL=0 to keep the (faster, stricter) binary path.
def _pg_compat_settings() -> tuple[str, ...]:
    use_text = os.environ.get("FORGE_PG_TEXT_PROTOCOL", "1").lower() not in ("0", "false", "no")
    stmts = ["SET pg_array_as_varchar = true"]
    if use_text:
        stmts.append("SET pg_use_binary_copy = false")
    return tuple(stmts)


def apply_pg_compat(con: duckdb.DuckDBPyConnection) -> None:
    """Best-effort: make DuckDB's Postgres scanner tolerant of arbitrary
    customer schemas. No-op on a connection where the postgres extension
    isn't loaded, or an older extension without a given knob."""
    for stmt in _pg_compat_settings():
        with contextlib.suppress(duckdb.Error):
            con.execute(stmt)


def _connect_and_attach_with_retry(source: str) -> duckdb.DuckDBPyConnection:
    """A pooling proxy in front of Postgres (e.g. Supabase's Supavisor) can
    occasionally serve a stale catalog snapshot right after a role/schema was
    just created elsewhere - ATTACH's own schema introspection then fails
    with something like `ERROR: invalid role OID` even though the
    credentials and schema are entirely valid. Rare and self-resolving within
    a few seconds, so a short retry clears it up instead of failing the whole
    run over a transient proxy hiccup. Reconnects from scratch each attempt -
    a connection that failed partway through ATTACH's own (multi-statement)
    introspection query isn't assumed to be reusable."""
    source = normalize_pg_url(source)
    last_error: Exception | None = None
    for attempt in range(_ATTACH_RETRY_ATTEMPTS):
        con = duckdb.connect(":memory:")
        con.execute("SET enable_progress_bar = false")
        try:
            con.execute("INSTALL postgres; LOAD postgres;")
            apply_pg_compat(con)
            con.execute(f"ATTACH '{source}' AS {CATALOG_ALIAS} (TYPE POSTGRES, READ_ONLY)")
            return con
        except duckdb.Error as exc:
            last_error = exc
            con.close()
            if attempt < _ATTACH_RETRY_ATTEMPTS - 1:
                time.sleep(_ATTACH_RETRY_BASE_DELAY_S * (attempt + 1))
    assert last_error is not None
    raise last_error


def redact(connection_string: str) -> str:
    """Never let a raw password reach a log line, an error message, or the
    input to a stable-id hash."""
    parsed = urlparse(connection_string)
    if not parsed.password:
        return connection_string
    redacted_netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
    return connection_string.replace(parsed.netloc, redacted_netloc, 1)


def extract_schema(connection_string: str, default: str = DEFAULT_SCHEMA) -> str:
    """Pull the target schema out of a `?options=-csearch_path%3D<schema>`
    query parameter, if present - this is how the client-warehouse loader
    (`forge_core.ingestion.warehouse`) tells us which per-client schema to
    scan, without changing what a plain customer connection string looks
    like. Falls back to `default` ('public') for every connection string
    that doesn't set this, which is all of them before this feature existed.
    """
    parsed = urlparse(connection_string)
    options = parse_qs(parsed.query).get("options", [None])[0]
    if not options:
        return default
    match = SEARCH_PATH_PATTERN.search(options)
    if not match:
        return default
    # search_path can list multiple schemas; we only ever set one.
    return match.group(1).strip('"').split(",")[0]


class PostgresAdapter:
    """Not a `Path`-based `IngestionAdapter` - see module docstring. The
    registry dispatches to this one by URL scheme before ever touching the
    filesystem, so it doesn't implement that ABC."""

    credential_env_var = CREDENTIAL_ENV_VAR

    def supports(self, source: str) -> bool:
        return bool(URL_SCHEME_PATTERN.match(source))

    def ingest(self, source: str, schema: str | None = None) -> DataSource:
        """`schema` defaults to whatever `extract_schema()` finds in `source`
        (itself defaulting to `'public'`) - see that function's docstring.
        Callers may still pass it explicitly if they already know it."""
        source = normalize_pg_url(source)
        if schema is None:
            schema = extract_schema(source)

        con = _connect_and_attach_with_retry(source)

        table_names = [
            row[0]
            for row in con.execute(
                f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{CATALOG_ALIAS}' "
                f"AND schema_name = '{schema}' ORDER BY table_name"
            ).fetchall()
        ]
        if not table_names:
            raise ValueError(f"No tables found via {redact(source)} (schema '{schema}' scanned).")

        # One un-scannable relation (a broken view with a bad cast, a column of
        # a type DuckDB can't read, a permission gap) must not abort a 50-table
        # ingest - skip it and carry on, but tell the operator which and why.
        tables = []
        skipped: list[str] = []
        for name in table_names:
            try:
                tables.append(
                    describe_relation(con, name, f'{CATALOG_ALIAS}."{schema}"."{name}"')
                )
            except Exception as exc:  # noqa: BLE001 - DuckDB/psql raise many types here
                first_line = str(exc).strip().splitlines()[0][:300]
                skipped.append(f"{name} ({first_line})")
        if skipped:
            _logger.warning(
                "Skipped %d unreadable relation(s) in schema %r: %s",
                len(skipped), schema, "; ".join(skipped),
            )
        if not tables:
            raise ValueError(
                f"Every relation in schema '{schema}' failed to introspect via "
                f"{redact(source)}. First: {skipped[0] if skipped else 'unknown'}"
            )
        table_names = [t.name for t in tables]
        total_rows = sum(t.row_count for t in tables)
        source_id = stable_source_id(redact(source), schema, *table_names)

        # Make the credential resolvable for the rest of this run so later
        # re-opens (profiling, dry_run, dashboard generation) resolve the
        # ${FORGE_SOURCE_DB_URL} placeholder the same way the shipped plugin
        # will - see forge_core.runtime_session._resolve_env_vars. Goes into
        # the run's credential overlay when one is active (the API, concurrent
        # runs), else os.environ (the CLI).
        remember(CREDENTIAL_ENV_VAR, source)

        return DataSource(
            id=source_id,
            kind=SourceKind.POSTGRES,
            tables=tables,
            connection=ConnectionContract(
                kind=SourceKind.POSTGRES,
                duckdb_attach_sql=[
                    "INSTALL postgres; LOAD postgres;",
                    f"ATTACH '${{{CREDENTIAL_ENV_VAR}}}' AS {CATALOG_ALIAS} (TYPE POSTGRES, READ_ONLY)",
                ],
                read_only=True,
                credential_env_vars=[CREDENTIAL_ENV_VAR],
                original_paths=[],
            ),
            total_row_count=total_rows,
            ingested_at=now_iso(),
        )


__all__ = [
    "CREDENTIAL_ENV_VAR",
    "DEFAULT_SCHEMA",
    "URL_SCHEME_PATTERN",
    "PostgresAdapter",
    "apply_pg_compat",
    "extract_schema",
    "normalize_pg_url",
    "redact",
]
