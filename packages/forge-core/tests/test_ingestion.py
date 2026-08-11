from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.models.common import SourceKind


def test_single_csv_becomes_one_table(bookings_csv: Path):
    ds = ingest(bookings_csv)
    assert ds.kind == SourceKind.CSV
    assert len(ds.tables) == 1
    table = ds.tables[0]
    assert table.row_count == 20
    assert "amount_inr" in {c.name for c in table.columns}
    assert len(table.sample_rows) == 5


def test_directory_of_csvs_becomes_multi_table(retail_orders_dir: Path):
    ds = ingest(retail_orders_dir)
    assert len(ds.tables) == 3
    names = {t.name for t in ds.tables}
    assert names == {"customers", "orders", "order_items"}
    orders = ds.table("orders")
    assert orders.row_count == 20
    assert ds.total_row_count == sum(t.row_count for t in ds.tables)


def test_sqlite_multi_table(edtech_sqlite: Path):
    ds = ingest(edtech_sqlite)
    assert ds.kind == SourceKind.SQLITE
    names = {t.name for t in ds.tables}
    assert names == {"students", "courses", "enrollments"}
    assert ds.table("enrollments").row_count == 10
    assert ds.connection.read_only is True
    assert any("READ_ONLY" in stmt for stmt in ds.connection.duckdb_attach_sql)


def test_connection_contract_uses_data_dir_placeholder(bookings_csv: Path):
    ds = ingest(bookings_csv)
    assert any("{DATA_DIR}" in stmt for stmt in ds.connection.duckdb_attach_sql)
    assert str(bookings_csv.resolve()) in ds.connection.original_paths


def test_unsupported_source_raises(tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    import pytest

    with pytest.raises(ValueError):
        ingest(bad)
