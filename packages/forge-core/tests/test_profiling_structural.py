from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.models.common import ColumnRole
from forge_core.profiling import build_structural_only


def _col(profile, table, name):
    return next(c for c in profile.columns if c.table == table and c.name == name)


def test_bookings_roles_and_pii(bookings_csv: Path, monkeypatch):
    # PII protection defaults off during testing (FORGE_ENABLE_PII_
    # PROTECTION) - explicitly on here to verify the underlying detection
    # logic itself still works; see test_pii_protection_defaults_off below
    # for the default this test is deliberately overriding.
    monkeypatch.setenv("FORGE_ENABLE_PII_PROTECTION", "true")
    ds = ingest(bookings_csv)
    profile = build_structural_only(ds)

    booking_id = _col(profile, "bookings", "booking_id")
    assert booking_id.guessed_role == ColumnRole.IDENTIFIER
    assert booking_id.is_likely_identifier

    # CURRENCY is no longer assignable by name (it's a semantic claim, not a
    # shape) - a plain numeric column is NUMERIC until an agent claim (gate-
    # verified) says otherwise. See profiling/structural.py's _guess_role.
    amount = _col(profile, "bookings", "amount_inr")
    assert amount.guessed_role == ColumnRole.NUMERIC

    phone = _col(profile, "bookings", "phone")
    assert phone.is_likely_pii

    name_col = _col(profile, "bookings", "customer_name")
    assert name_col.is_likely_pii


def test_pii_protection_defaults_off(bookings_csv: Path, monkeypatch):
    """No column is ever denied/redacted during testing unless
    FORGE_ENABLE_PII_PROTECTION is explicitly turned on - see this same
    fixture's phone/customer_name columns flagged True in the test above
    once the flag is set."""
    monkeypatch.delenv("FORGE_ENABLE_PII_PROTECTION", raising=False)
    ds = ingest(bookings_csv)
    profile = build_structural_only(ds)
    assert not any(c.is_likely_pii for c in profile.columns)


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
