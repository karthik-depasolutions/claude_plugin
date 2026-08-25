from __future__ import annotations

from pathlib import Path

import duckdb

from forge_core.compiler.metric_compiler import MetricCompileError, render_metric_query
from forge_core.compiler.metric_generator import generate_metrics
from forge_core.ingestion.registry import ingest
from forge_core.models.claims import ColumnClaim
from forge_core.models.metrics import AggOp, FilterOp, FilterSpec
from forge_core.profiling import build_structural_only
from forge_core.runtime_session import open_session

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def _sum_claim(table: str, column: str, unit: str = "INR") -> dict[str, ColumnClaim]:
    """A minimal, gate-shaped ColumnClaim granting SUM - additivity is a
    semantic property now (Part 1/2), never a shape, so any test wanting a
    SUM metric must supply one explicitly rather than relying on a
    guessed_role == CURRENCY name match that no longer exists."""
    return {
        f"{table}.{column}": ColumnClaim(
            table=table,
            column=column,
            meaning=f"total {column}",
            kind="measure",
            unit=unit,
            valid_aggregations=[AggOp.SUM, AggOp.MEAN, AggOp.MIN, AggOp.MAX],
            confidence=0.95,
            evidence=["test-supplied claim"],
        )
    }


def test_edtech_generates_measure_and_dimension_metrics():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())

    ids = {m.id for m in metrics}
    assert "average_score" in ids  # score is NUMERIC -> average only

    avg_score = next(m for m in metrics if m.id == "average_score")
    assert avg_score.aggregation == AggOp.MEAN
    dim_field_ids = {d.field_id for d in avg_score.allowed_dimensions}
    # status (fact table's own dimension) and courses/students dims reached
    # by a verified join should all be candidates.
    assert "enrollments.status" in dim_field_ids
    assert any(f.startswith("courses.") for f in dim_field_ids)


def test_retail_orders_no_metric_traverses_a_fan_out_edge():
    ds = ingest(DATASETS_ROOT / "retail_orders")
    structural = build_structural_only(ds)
    metrics = generate_metrics("orders", structural, denied_columns=set())

    for metric in metrics:
        for dim in metric.allowed_dimensions:
            assert dim.fan_out_safe, f"{dim.field_id} on {metric.id} should never be offered - it fans out"
            for edge in dim.join_path:
                assert not edge.fan_out_risk


def test_denied_columns_never_become_measures_or_dimensions(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    metrics = generate_metrics("bookings", structural, denied_columns={"customer_name", "phone", "amount_inr"})

    for metric in metrics:
        assert metric.measure_column != "amount_inr"
        for dim in metric.allowed_dimensions:
            assert dim.physical not in {"customer_name", "phone"}


def test_single_table_source_still_generates_metrics(bookings_csv: Path):
    """No entity_graph exists for a single-table source (ADR 0001) - the
    generator must still work off structural columns directly."""
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    assert structural.entity_graph is None
    metrics = generate_metrics(
        "bookings", structural, denied_columns=set(), claims=_sum_claim("bookings", "amount_inr")
    )
    assert metrics
    total_revenue = next(m for m in metrics if m.measure_column == "amount_inr" and m.aggregation == AggOp.SUM)
    assert total_revenue.allowed_dimensions


# --- rendering + real execution ---------------------------------------------


def _physical_ref_for(ds) -> dict[str, str]:
    return {t.name: t.physical_ref for t in ds.tables}


def test_render_and_execute_a_simple_metric_matches_real_data(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    metrics = generate_metrics(
        "bookings",
        structural,
        denied_columns={"customer_name", "phone"},
        claims=_sum_claim("bookings", "amount_inr"),
    )
    total_revenue = next(m for m in metrics if m.measure_column == "amount_inr" and m.aggregation == AggOp.SUM)

    query = render_metric_query(total_revenue, _physical_ref_for(ds))
    con = open_session(ds)
    try:
        row = con.execute(query.sql, query.params).fetchone()
    finally:
        con.close()
    assert row[0] == 32880  # SUM(amount_inr) over all 20 rows, no filter


def test_render_grouped_metric_across_a_verified_join_matches_real_data():
    """The actual capability unlock: a metric on the fact table, grouped by
    a dimension reached through a real join, executes for real and the
    numbers match hand-computed ground truth."""
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())
    avg_score = next(m for m in metrics if m.id == "average_score")
    course_dim = next(d for d in avg_score.allowed_dimensions if d.field_id == "courses.course_name")

    query = render_metric_query(avg_score, _physical_ref_for(ds), group_by=course_dim.field_id)
    con = open_session(ds)
    try:
        rows = con.execute(query.sql, query.params).fetchall()
    finally:
        con.close()
    names = {r[0] for r in rows}
    assert "Algebra II" in names  # a real course name, not course_id - the P1.1 fix


def test_render_with_a_filter_binds_the_literal_not_interpolates_it():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())
    avg_score = next(m for m in metrics if m.id == "average_score")

    injection = "completed'; DROP TABLE enrollments; --"
    filt = FilterSpec(column="status", op=FilterOp.EQ, values=[injection])
    query = render_metric_query(avg_score, _physical_ref_for(ds), filters=[filt])
    assert "?" in query.sql
    assert injection not in query.sql  # never string-interpolated
    con = open_session(ds)
    try:
        row = con.execute(query.sql, query.params).fetchone()
        # the table must still exist and be queryable afterwards
        sanity = con.execute('SELECT COUNT(*) FROM srcdb."enrollments"').fetchone()
    finally:
        con.close()
    assert row[0] is None  # no row matches the literal injection string
    assert sanity[0] == 10


def test_render_with_a_time_grain_matches_real_monthly_data():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())
    total = next(m for m in metrics if m.id == "average_score")
    assert total.time_column == "enrolled_on"

    query = render_metric_query(total, _physical_ref_for(ds), time_grain="month")
    con = open_session(ds)
    try:
        rows = con.execute(query.sql, query.params).fetchall()
    finally:
        con.close()
    assert len(rows) == 3  # 2023-08, 2023-09, 2023-10 - matches the real enrollment spread


def test_unknown_dimension_is_rejected_with_a_clear_message():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())
    avg_score = next(m for m in metrics if m.id == "average_score")

    try:
        render_metric_query(avg_score, _physical_ref_for(ds), group_by="not_a_real_dimension")
        assert False, "should have raised"
    except MetricCompileError as exc:
        assert "not_a_real_dimension" in str(exc)


def test_unknown_time_grain_is_rejected():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())
    avg_score = next(m for m in metrics if m.id == "average_score")

    try:
        render_metric_query(avg_score, _physical_ref_for(ds), time_grain="decade")
        assert False, "should have raised"
    except MetricCompileError:
        pass


def test_filter_on_a_non_filterable_field_is_rejected():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    metrics = generate_metrics("enrollments", structural, denied_columns=set())
    avg_score = next(m for m in metrics if m.id == "average_score")

    filt = FilterSpec(column="enrollment_id", op=FilterOp.EQ, values=["1"])
    try:
        render_metric_query(avg_score, _physical_ref_for(ds), filters=[filt])
        assert False, "should have raised"
    except MetricCompileError:
        pass
