"""File-based ingestion: CSV, TSV, Excel, JSON, NDJSON, Parquet.

A single file becomes a one-table DataSource. A directory of files becomes a
multi-table DataSource — one table per file, named after the file stem. This
is how `fixtures/datasets/retail_orders/` (orders.csv + customers.csv +
line_items.csv) exercises the multi-table / join-detection path without
requiring a live database.

Every DuckDB read function is *_auto so column types are sniffed rather than
assumed all-string, which matters for the deterministic profiler downstream.

`ConnectionContract.duckdb_attach_sql` uses the `{DATA_DIR}` placeholder
instead of an absolute path, because the file will move at least twice more
(into `generated/<run>/data/`, then into the packaged plugin's `data/`). The
packager and the MCP runtime are the only two places that ever substitute
this token — see forge_core.packaging.plugin_builder and
mis_mcp_runtime.config.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from forge_core.ingestion.base import (
    IngestionAdapter,
    describe_relation,
    now_iso,
    sanitize_table_name,
    stable_source_id,
)
from forge_core.models.common import SourceKind
from forge_core.models.datasource import ConnectionContract, DataSource

_EXTENSION_KIND: dict[str, SourceKind] = {
    ".csv": SourceKind.CSV,
    ".tsv": SourceKind.TSV,
    ".xlsx": SourceKind.EXCEL,
    ".xls": SourceKind.EXCEL,
    ".json": SourceKind.JSON,
    ".ndjson": SourceKind.NDJSON,
    ".jsonl": SourceKind.NDJSON,
    ".parquet": SourceKind.PARQUET,
}


def _read_sql_for(path: Path, data_dir_token: str = "{DATA_DIR}") -> tuple[str, str]:
    """Returns (concrete_sql_for_now, templated_sql_for_config) for one file."""

    ext = path.suffix.lower()
    filename = path.name
    if ext == ".csv":
        concrete = f"read_csv_auto('{path.as_posix()}')"
        templated = f"read_csv_auto('{data_dir_token}/{filename}')"
    elif ext == ".tsv":
        concrete = f"read_csv_auto('{path.as_posix()}', delim='\\t')"
        templated = f"read_csv_auto('{data_dir_token}/{filename}', delim='\\t')"
    elif ext in (".json", ".ndjson", ".jsonl"):
        concrete = f"read_json_auto('{path.as_posix()}')"
        templated = f"read_json_auto('{data_dir_token}/{filename}')"
    elif ext == ".parquet":
        concrete = f"read_parquet('{path.as_posix()}')"
        templated = f"read_parquet('{data_dir_token}/{filename}')"
    else:
        raise ValueError(f"Unsupported file extension for direct DuckDB read: {ext}")
    return concrete, templated


def _load_excel_as_view(con: duckdb.DuckDBPyConnection, path: Path, view_name: str) -> None:
    import pandas as pd

    df = pd.read_excel(path)
    con.register(view_name, df)


class FileAdapter(IngestionAdapter):
    """Handles a single file or a directory of files."""

    def supports(self, source_path: Path) -> bool:
        if source_path.is_dir():
            return any(p.suffix.lower() in _EXTENSION_KIND for p in source_path.iterdir())
        return source_path.suffix.lower() in _EXTENSION_KIND

    def ingest(self, source_path: Path) -> DataSource:
        con = duckdb.connect(":memory:")
        files = self._resolve_files(source_path)

        tables = []
        attach_statements: list[str] = []
        kinds_seen: set[SourceKind] = set()

        for file_path in files:
            ext = file_path.suffix.lower()
            kind = _EXTENSION_KIND[ext]
            kinds_seen.add(kind)
            table_name = sanitize_table_name(file_path.stem)
            view_name = f"src_{table_name}"

            if kind == SourceKind.EXCEL:
                _load_excel_as_view(con, file_path, view_name)
                # Excel has no DuckDB read_* equivalent; the runtime loads it
                # via pandas too. Record that explicitly for the runtime config.
                attach_statements.append(f"-- excel:{view_name}:{{DATA_DIR}}/{file_path.name}")
            else:
                concrete_sql, templated_sql = _read_sql_for(file_path)
                con.execute(f"CREATE VIEW {view_name} AS SELECT * FROM {concrete_sql}")
                attach_statements.append(f"CREATE VIEW {view_name} AS SELECT * FROM {templated_sql}")

            tables.append(describe_relation(con, table_name, view_name))

        primary_kind = kinds_seen.pop() if len(kinds_seen) == 1 else SourceKind.CSV
        total_rows = sum(t.row_count for t in tables)
        source_id = stable_source_id(str(source_path.resolve()), *[t.name for t in tables])

        return DataSource(
            id=source_id,
            kind=primary_kind,
            tables=tables,
            connection=ConnectionContract(
                kind=primary_kind,
                duckdb_attach_sql=attach_statements,
                read_only=True,
                original_paths=[str(f.resolve()) for f in files],
            ),
            total_row_count=total_rows,
            ingested_at=now_iso(),
        )

    @staticmethod
    def _resolve_files(source_path: Path) -> list[Path]:
        if source_path.is_file():
            return [source_path]
        files = sorted(
            p for p in source_path.iterdir() if p.is_file() and p.suffix.lower() in _EXTENSION_KIND
        )
        if not files:
            raise ValueError(f"No supported files found in directory: {source_path}")
        return files
