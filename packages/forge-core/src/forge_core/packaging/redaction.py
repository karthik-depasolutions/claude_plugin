"""Part of Stage 7 (PACKAGE) — writes each table's data into the plugin's
`data/` directory with every denied column (a role category the industry
pack's guardrails deny, e.g. `free_text`) dropped from the bytes on disk,
not just excluded from compiled SQL.

Why this exists: the runtime's denied-column policy only gates what a
*query* can return. Before this module, the raw source file was copied
into the plugin verbatim — a denied column that no KPI or `run_safe_query`
call ever projects would still sit in the packaged plugin's `data/` folder
in plaintext.

Reuses `forge_core.runtime_session.open_session` (the same DuckDB
reconnection logic the profiler/dry-run/validation harness already use) so
every source kind - single file, a directory of files, or a SQLite
database - is handled through one `SELECT * EXCLUDE (...)` per table,
written back out in the same filename/format the runtime's
`duckdb_attach_sql` already expects.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from forge_core.models.common import SourceKind
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import SchemaProfile
from forge_core.runtime_session import open_session

_TEXT_EXTENSIONS = {".csv", ".tsv", ".json", ".ndjson", ".jsonl", ".parquet"}
_EXCEL_EXTENSIONS = {".xlsx", ".xls"}


def denied_columns_by_table(profile: SchemaProfile, pack: IndustryPack) -> dict[str, set[str]]:
    """Every column, across *all* tables (not just the fact table `resolve_
    bindings` binds KPIs against), whose structural role category the pack's
    guardrails deny outright (e.g. `free_text`)."""
    denied: dict[str, set[str]] = {}
    for col in profile.structural.columns:
        if col.guessed_role.value in pack.guardrails.denied_role_categories:
            denied.setdefault(col.table, set()).add(col.name)
    return denied


def _exclude_clause(columns: set[str]) -> str:
    if not columns:
        return ""
    quoted = ", ".join(f'"{c}"' for c in sorted(columns))
    return f" EXCLUDE ({quoted})"


def write_redacted_data_files(
    source: DataSource, profile: SchemaProfile, pack: IndustryPack, output_dir: Path
) -> None:
    """Writes every table under `<output_dir>/data/`, redacted per `pack`'s
    guardrails. Replaces a plain file copy - callers should use this instead
    of copying `source.connection.original_paths` directly.

    A live-database source (e.g. Postgres, see forge_core.ingestion.postgres)
    has no `original_paths` - there is nothing to copy, by design: the
    shipped plugin never holds a snapshot of the data, it connects live via
    a credential env var at query time (config/data_source.json only ever
    stores the env var's *name*)."""
    if not source.connection.original_paths:
        return
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    denied_by_table = denied_columns_by_table(profile, pack)

    con = open_session(source)
    try:
        if source.kind == SourceKind.SQLITE:
            _write_redacted_sqlite(con, source, denied_by_table, data_dir)
        else:
            _write_redacted_files(con, source, denied_by_table, data_dir)
    finally:
        con.close()


def _write_redacted_files(
    con: duckdb.DuckDBPyConnection,
    source: DataSource,
    denied_by_table: dict[str, set[str]],
    data_dir: Path,
) -> None:
    # FileAdapter builds `tables` and `original_paths` from the same
    # file-by-file loop, in the same order - see forge_core.ingestion.files.
    for original_path_str, table in zip(source.connection.original_paths, source.tables, strict=True):
        original_path = Path(original_path_str)
        destination = data_dir / original_path.name
        exclude = _exclude_clause(denied_by_table.get(table.name, set()))
        select_sql = f"SELECT *{exclude} FROM {table.physical_ref}"
        _export_table(con, select_sql, original_path.suffix.lower(), destination)


def _export_table(
    con: duckdb.DuckDBPyConnection, select_sql: str, extension: str, destination: Path
) -> None:
    if extension == ".csv":
        con.execute(f"COPY ({select_sql}) TO '{destination.as_posix()}' (HEADER, DELIMITER ',')")
    elif extension == ".tsv":
        con.execute(f"COPY ({select_sql}) TO '{destination.as_posix()}' (HEADER, DELIMITER '\t')")
    elif extension == ".parquet":
        con.execute(f"COPY ({select_sql}) TO '{destination.as_posix()}' (FORMAT PARQUET)")
    elif extension == ".json":
        con.execute(f"COPY ({select_sql}) TO '{destination.as_posix()}' (FORMAT JSON, ARRAY true)")
    elif extension in (".ndjson", ".jsonl"):
        con.execute(f"COPY ({select_sql}) TO '{destination.as_posix()}' (FORMAT JSON, ARRAY false)")
    elif extension in _EXCEL_EXTENSIONS:
        con.execute(select_sql).df().to_excel(destination, index=False)
    else:
        raise ValueError(f"Don't know how to write a redacted copy of a {extension!r} file.")


def _write_redacted_sqlite(
    con: duckdb.DuckDBPyConnection,
    source: DataSource,
    denied_by_table: dict[str, set[str]],
    data_dir: Path,
) -> None:
    original_path = Path(source.connection.original_paths[0])
    destination = data_dir / original_path.name
    destination.unlink(missing_ok=True)
    con.execute(f"ATTACH '{destination.as_posix()}' AS redacted (TYPE SQLITE)")
    try:
        for table in source.tables:
            exclude = _exclude_clause(denied_by_table.get(table.name, set()))
            select_sql = f"SELECT *{exclude} FROM {table.physical_ref}"
            con.execute(f'CREATE TABLE redacted."{table.name}" AS {select_sql}')
    finally:
        con.execute("DETACH redacted")


__all__ = ["denied_columns_by_table", "write_redacted_data_files"]
