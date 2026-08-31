"""Phase 1 deterministic-understanding upgrades: relationship recall,
composite-key grain, value-set capture, and statistical pattern mining.
"""

from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.profiling import build_structural_only


def _profile(path: Path):
    return build_structural_only(ingest(path))


def test_value_sets_captured_for_low_cardinality_column(bookings_csv: Path):
    prof = _profile(bookings_csv)
    assert "bookings.status" in prof.value_sets
    assert len(prof.value_sets["bookings.status"]) >= 2
    # a high-cardinality id column is never captured as a value set
    assert "bookings.booking_id" not in prof.value_sets


def test_temporal_pattern_on_a_date_column(bookings_csv: Path):
    prof = _profile(bookings_csv)
    dated = {p.column for p in prof.patterns.temporal if p.table == "bookings"}
    assert "booking_date" in dated
    pattern = next(p for p in prof.patterns.temporal if p.column == "booking_date")
    assert pattern.trend in ("rising", "falling", "flat")
    assert sum(pattern.buckets.values()) > 0


def test_composite_key_grain_when_no_single_column_is_unique(tmp_path: Path):
    csv = tmp_path / "attendance.csv"
    rows = ["student,day,present"]
    for student in ("alice", "bob", "carol"):
        for day in ("mon", "tue", "wed"):
            rows.append(f"{student},{day},yes")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    grain = next(g for g in _profile(csv).grains if g.table == "attendance")
    assert set(grain.grain_columns) == {"student", "day"}
    assert grain.confidence == 0.7


def test_weak_relationship_is_flagged_when_child_has_orphans(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "customers.csv").write_text(
        "customer_id,name\n" + "\n".join(f"{i},c{i}" for i in range(1, 21)) + "\n", encoding="utf-8"
    )
    # 16/20 orders point at a real customer, 4 are orphans -> 0.80 <= overlap < 0.95
    order_lines = [f"{i},{(i % 20) + 1}" for i in range(16)] + [f"{i},{900 + i}" for i in range(4)]
    (src / "orders.csv").write_text("order_id,customer_id\n" + "\n".join(order_lines) + "\n", encoding="utf-8")

    rels = _profile(src).relationships
    edge = next(r for r in rels if r.from_table == "orders" and r.to_table == "customers")
    assert edge.strength == "weak"
    assert 0.80 <= edge.confidence < 0.95


def test_unrelated_tables_yield_no_relationships(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "weather.csv").write_text("city,temp_c\nA,20\nB,25\nC,30\n", encoding="utf-8")
    (src / "recipes.csv").write_text("dish,minutes\nsoup,30\nsalad,10\ntoast,5\n", encoding="utf-8")

    assert _profile(src).relationships == []


def test_temporal_pattern_has_day_of_week_and_yoy(bookings_csv: Path):
    prof = _profile(bookings_csv)
    p = next(x for x in prof.patterns.temporal if x.column == "booking_date")
    assert set(p.day_of_week) <= {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    assert sum(p.day_of_week.values()) > 0
    assert all(isinstance(v, float) for v in p.year_over_year.values())


def test_segments_break_a_dimension_into_shares(bookings_csv: Path):
    segs = _profile(bookings_csv).patterns.segments
    assert segs, "bookings has categorical dimensions to segment on"
    s = segs[0]
    assert s.table == "bookings"
    assert 0.0 < s.top_groups[0][1] <= 1.0
    assert s.concentration in ("high", "moderate", "even")


def test_denormalization_mismatch_is_detected(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    # order 4's stored total (999) disagrees with re-summing its items (10).
    (src / "orders.csv").write_text(
        "order_id,order_total\n1,100.0\n2,60.0\n3,45.0\n4,999.0\n5,80.0\n", encoding="utf-8"
    )
    items = "\n".join(
        [
            "item_id,order_id,line_amount,line_qty",
            "1,1,30.0,2",  # 60
            "2,1,40.0,1",  # +40 -> 100  ok
            "3,2,20.0,3",  # 60  ok
            "4,3,15.0,3",  # 45  ok
            "5,4,10.0,1",  # 10  != 999
            "6,5,16.0,5",  # 80  ok
        ]
    )
    (src / "order_items.csv").write_text(items + "\n", encoding="utf-8")

    mism = _profile(src).patterns.mismatches
    assert any(
        m.parent_table == "orders"
        and m.parent_column == "order_total"
        and m.child_table == "order_items"
        and m.mismatched_rows == 1
        and "line_qty" in m.child_expression
        for m in mism
    )
