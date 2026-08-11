from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.models.common import ColumnRole
from forge_core.profiling import build_structural_only


def _col(profile, table, name):
    return next(c for c in profile.columns if c.table == table and c.name == name)


def test_bookings_roles_and_pii(bookings_csv: Path):
    ds = ingest(bookings_csv)
    profile = build_structural_only(ds)

    booking_id = _col(profile, "bookings", "booking_id")
    assert booking_id.guessed_role == ColumnRole.IDENTIFIER
    assert booking_id.is_likely_identifier

    amount = _col(profile, "bookings", "amount_inr")
    assert amount.guessed_role == ColumnRole.CURRENCY

    phone = _col(profile, "bookings", "phone")
    assert phone.is_likely_pii

    name_col = _col(profile, "bookings", "customer_name")
    assert name_col.is_likely_pii


def test_retail_multi_table_relationships_detected(retail_orders_dir: Path):
    ds = ingest(retail_orders_dir)
    profile = build_structural_only(ds)

    rel_pairs = {(r.from_table, r.from_column, r.to_table, r.to_column) for r in profile.relationships}
    assert ("orders", "customer_id", "customers", "customer_id") in rel_pairs
    assert ("order_items", "order_id", "orders", "order_id") in rel_pairs

    customers_grain = next(g for g in profile.grains if g.table == "customers")
    assert customers_grain.grain_columns == ["customer_id"]


def test_sqlite_multi_table_grains(edtech_sqlite: Path):
    ds = ingest(edtech_sqlite)
    profile = build_structural_only(ds)
    students_grain = next(g for g in profile.grains if g.table == "students")
    assert "student_id" in students_grain.grain_columns

    rel_pairs = {(r.from_table, r.from_column, r.to_table, r.to_column) for r in profile.relationships}
    assert ("enrollments", "student_id", "students", "student_id") in rel_pairs
    assert ("enrollments", "course_id", "courses", "course_id") in rel_pairs
