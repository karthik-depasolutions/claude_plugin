"""Ingestion adapter contract.

Every adapter turns some physical source into a `DataSource`. The only rule
that matters: nothing downstream of ingestion should ever need to know what
kind of source it was. See docs/architecture.md §4.1.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from forge_core.models.datasource import DataSource, RawColumn, TableDescriptor

SAMPLE_ROWS = 5


def stable_source_id(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def describe_relation(con: duckdb.DuckDBPyConnection, table_name: str, ref_sql: str) -> TableDescriptor:
    """Introspect a DuckDB-queryable relation (view, attached table, etc.)
    into a TableDescriptor. `ref_sql` is what to put in a FROM clause."""

    columns_info = con.execute(f"DESCRIBE SELECT * FROM {ref_sql}").fetchall()
    columns = [
        RawColumn(name=row[0], raw_dtype=row[1], ordinal=i) for i, row in enumerate(columns_info)
    ]
    count_row = con.execute(f"SELECT COUNT(*) FROM {ref_sql}").fetchone()
    assert count_row is not None
    row_count = count_row[0]
    sample_df = con.execute(f"SELECT * FROM {ref_sql} LIMIT {SAMPLE_ROWS}").fetchdf()
    sample_rows = sample_df.where(sample_df.notnull(), None).to_dict(orient="records")
    return TableDescriptor(
        name=table_name,
        physical_ref=ref_sql,
        row_count=int(row_count),
        columns=columns,
        sample_rows=sample_rows,
    )


def sanitize_table_name(stem: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in stem.strip().lower())
    cleaned = cleaned.strip("_") or "table"
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned


class IngestionAdapter(ABC):
    """One adapter per source kind. Registered in `registry.py`."""

    @abstractmethod
    def supports(self, source_path: Path) -> bool: ...

    @abstractmethod
    def ingest(self, source_path: Path) -> DataSource: ...
