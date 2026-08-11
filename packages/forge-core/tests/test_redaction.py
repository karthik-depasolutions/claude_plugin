"""Denied columns (PII, or any role category a pack's guardrails deny) must
never reach the plugin's `data/` folder on disk - not just be excluded from
compiled SQL. Covers every source shape the ingestion layer supports: a
single file, a directory of files (join dataset, denied column on a
non-fact table), and a SQLite database (denied column on a non-fact table,
inside the one physical file)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from forge_core.classification import load_pack
from forge_core.ingestion.registry import ingest
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging.redaction import denied_columns_by_table, write_redacted_data_files
from forge_core.profiling import build_structural_only

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKS_ROOT = REPO_ROOT / "industry-packs"
FIXTURES_ROOT = REPO_ROOT / "fixtures" / "datasets"


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def test_denied_columns_are_collected_across_every_table_not_just_the_fact_table():
    profile = _profile_for(FIXTURES_ROOT / "retail_orders")
    pack = load_pack(PACKS_ROOT / "retail-ecommerce")

    denied = denied_columns_by_table(profile, pack)

    # customers.csv is a dimension table, not the `orders` fact table that
    # resolve_bindings' own denied_columns is scoped to - it must still show
    # up here, or its PII would ship in the plugin unredacted.
    assert "email_address" in denied.get("customers", set())
    assert "full_name" in denied.get("customers", set())


def test_write_redacted_data_files_strips_pii_from_every_csv_in_a_multi_table_source(tmp_path: Path):
    profile = _profile_for(FIXTURES_ROOT / "retail_orders")
    pack = load_pack(PACKS_ROOT / "retail-ecommerce")

    write_redacted_data_files(profile.source, profile, pack, tmp_path)

    customers_header = (tmp_path / "data" / "customers.csv").read_text(encoding="utf-8").splitlines()[0]
    columns = customers_header.split(",")
    assert "email_address" not in columns
    assert "full_name" not in columns
    assert "customer_id" in columns  # non-denied columns must survive

    # The other table in the same source is untouched (it has no denied columns).
    orders_header = (tmp_path / "data" / "orders.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "order_id" in orders_header.split(",")


def test_write_redacted_data_files_strips_pii_from_every_table_in_a_sqlite_file(tmp_path: Path):
    profile = _profile_for(FIXTURES_ROOT / "edtech.sqlite")
    pack = load_pack(PACKS_ROOT / "edtech")

    write_redacted_data_files(profile.source, profile, pack, tmp_path)

    redacted_db = tmp_path / "data" / "edtech.sqlite"
    assert redacted_db.is_file()

    con = duckdb.connect(":memory:")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{redacted_db.as_posix()}' AS check_db (TYPE SQLITE, READ_ONLY)")
    student_columns = [c[0] for c in con.execute('DESCRIBE check_db."students"').fetchall()]
    assert "email" not in student_columns
    assert "full_name" not in student_columns
    assert "student_id" in student_columns

    # Row data (not just the schema) round-tripped correctly.
    row_count = con.execute('SELECT COUNT(*) FROM check_db."enrollments"').fetchone()
    assert row_count is not None and row_count[0] > 0
    con.close()


def test_write_redacted_data_files_handles_the_plain_single_file_case(tmp_path: Path):
    profile = _profile_for(FIXTURES_ROOT / "bookings.csv")
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")

    write_redacted_data_files(profile.source, profile, pack, tmp_path)

    header = (tmp_path / "data" / "bookings.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "phone" not in header.split(",")


if __name__ == "__main__":
    pytest.main([__file__])
