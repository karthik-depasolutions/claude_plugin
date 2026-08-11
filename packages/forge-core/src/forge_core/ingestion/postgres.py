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

import os
import re
from urllib.parse import urlparse

import duckdb

from forge_core.ingestion.base import describe_relation, now_iso, stable_source_id
from forge_core.models.common import SourceKind
from forge_core.models.datasource import ConnectionContract, DataSource

CATALOG_ALIAS = "srcdb"
CREDENTIAL_ENV_VAR = "FORGE_SOURCE_DB_URL"
URL_SCHEME_PATTERN = re.compile(r"^postgres(ql)?://", re.IGNORECASE)


def redact(connection_string: str) -> str:
    """Never let a raw password reach a log line, an error message, or the
    input to a stable-id hash."""
    parsed = urlparse(connection_string)
    if not parsed.password:
        return connection_string
    redacted_netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
    return connection_string.replace(parsed.netloc, redacted_netloc, 1)


class PostgresAdapter:
    """Not a `Path`-based `IngestionAdapter` - see module docstring. The
    registry dispatches to this one by URL scheme before ever touching the
    filesystem, so it doesn't implement that ABC."""

    credential_env_var = CREDENTIAL_ENV_VAR

    def supports(self, source: str) -> bool:
        return bool(URL_SCHEME_PATTERN.match(source))

    def ingest(self, source: str) -> DataSource:
        con = duckdb.connect(":memory:")
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{source}' AS {CATALOG_ALIAS} (TYPE POSTGRES, READ_ONLY)")

        table_names = [
            row[0]
            for row in con.execute(
                f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{CATALOG_ALIAS}' "
                "AND schema_name = 'public' ORDER BY table_name"
            ).fetchall()
        ]
        if not table_names:
            raise ValueError(
                f"No tables found via {redact(source)} (only the 'public' schema is scanned)."
            )

        tables = [describe_relation(con, name, f'{CATALOG_ALIAS}."{name}"') for name in table_names]
        total_rows = sum(t.row_count for t in tables)
        source_id = stable_source_id(redact(source), *table_names)

        # Set for the rest of this process so later re-opens in the same
        # pipeline run (profiling, dry_run, dashboard generation) resolve
        # the placeholder the same way the shipped plugin will - see
        # forge_core.runtime_session._resolve_env_vars.
        os.environ[CREDENTIAL_ENV_VAR] = source

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


__all__ = ["CREDENTIAL_ENV_VAR", "URL_SCHEME_PATTERN", "PostgresAdapter", "redact"]
