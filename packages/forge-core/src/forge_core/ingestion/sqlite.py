"""SQLite ingestion via DuckDB's sqlite_scanner extension.

Read-only by construction: `ATTACH ... (TYPE SQLITE, READ_ONLY)`. This is the
same architectural pattern a future Postgres/MySQL adapter will follow —
DuckDB is the uniform query surface, and the customer's data is never
copied wholesale, only introspected and (during profiling) sampled.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from forge_core.ingestion.base import IngestionAdapter, describe_relation, now_iso, stable_source_id
from forge_core.models.common import SourceKind
from forge_core.models.datasource import ConnectionContract, DataSource

CATALOG_ALIAS = "srcdb"


class SqliteAdapter(IngestionAdapter):
    def supports(self, source_path: Path) -> bool:
        if source_path.is_dir() or source_path.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
            return False
        return _looks_like_sqlite(source_path)

    def ingest(self, source_path: Path) -> DataSource:
        con = duckdb.connect(":memory:")
        con.execute("SET enable_progress_bar = false")
        con.execute("INSTALL sqlite; LOAD sqlite;")
        abs_path = source_path.resolve()
        con.execute(f"ATTACH '{abs_path.as_posix()}' AS {CATALOG_ALIAS} (TYPE SQLITE, READ_ONLY)")

        table_names = [
            row[0]
            for row in con.execute(
                f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{CATALOG_ALIAS}' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        if not table_names:
            raise ValueError(f"No tables found in SQLite database: {source_path}")

        tables = [
            describe_relation(con, name, f'{CATALOG_ALIAS}."{name}"') for name in table_names
        ]
        total_rows = sum(t.row_count for t in tables)
        source_id = stable_source_id(str(abs_path), *table_names)

        attach_sql = f"ATTACH '{{DATA_DIR}}/{source_path.name}' AS {CATALOG_ALIAS} (TYPE SQLITE, READ_ONLY)"

        return DataSource(
            id=source_id,
            kind=SourceKind.SQLITE,
            tables=tables,
            connection=ConnectionContract(
                kind=SourceKind.SQLITE,
                duckdb_attach_sql=["INSTALL sqlite; LOAD sqlite;", attach_sql],
                read_only=True,
                original_paths=[str(abs_path)],
            ),
            total_row_count=total_rows,
            ingested_at=now_iso(),
        )


def _looks_like_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            header = f.read(16)
        return header[:15] == b"SQLite format 3"
    except OSError:
        return False
