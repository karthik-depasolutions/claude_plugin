from __future__ import annotations

from pathlib import Path

import pytest

from forge_core.agentic.investigation_tools import (
    AllowlistViolation,
    _Toolkit,
    _aggregate,
    _check_relationship,
    _compare_columns,
    _inspect_column,
    _sample_rows,
    _test_value_set,
    build_investigation_tools,
)
from forge_core.ingestion.registry import ingest
from forge_core.models.metrics import AggOp
from forge_core.profiling import build_structural_only

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def _toolkit_for(path: Path, denied: set[str] | None = None) -> _Toolkit:
    ds = ingest(path)
    structural = build_structural_only(ds)
    return _Toolkit(ds, structural, denied)


# --- inspect_column ----------------------------------------------------------


def test_inspect_column_returns_real_stats_for_score():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    detail = _inspect_column(toolkit, "enrollments", "score")
    assert detail.guessed_role == "numeric"
    assert detail.min_value == "67.5"
    assert detail.max_value == "95.5"
    assert detail.sample_values  # non-PII, real values returned


def test_inspect_column_never_returns_pii_values(bookings_csv: Path):
    toolkit = _toolkit_for(bookings_csv)
    detail = _inspect_column(toolkit, "bookings", "customer_name")
    assert detail.sample_values == []


def test_inspect_unknown_column_raises():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    with pytest.raises(AllowlistViolation):
        _inspect_column(toolkit, "enrollments", "not_a_real_column")


def test_inspect_denied_column_raises():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite", denied={"score"})
    with pytest.raises(AllowlistViolation):
        _inspect_column(toolkit, "enrollments", "score")


# --- compare_columns -----------------------------------------------------------


def test_compare_columns_side_by_side():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    comparison = _compare_columns(toolkit, "enrollments", ["score", "enrollment_id"])
    by_name = {c.column: c for c in comparison.columns}
    assert by_name["score"].guessed_role == "numeric"
    assert by_name["enrollment_id"].guessed_role == "identifier"


# --- check_relationship --------------------------------------------------------


def test_check_relationship_on_a_real_fk_is_verified():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    fact = _check_relationship(toolkit, "enrollments", "course_id", "courses", "course_id")
    assert fact.overlap_ratio == 1.0
    assert fact.verified is True
    assert fact.cardinality == "N:1"


def test_check_relationship_on_unrelated_columns_is_not_verified():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    fact = _check_relationship(toolkit, "enrollments", "score", "courses", "price_inr")
    assert fact.overlap_ratio < 0.5
    assert fact.verified is False


# --- test_value_set --------------------------------------------------------------


def test_value_set_catches_active_not_belonging_to_completed():
    """The exact edtech bug this whole tool exists to catch (review §9 V3):
    'active' must not silently count as a match just because a hint list
    says so - the tool only reports what's REALLY there."""
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    coverage = _test_value_set(toolkit, "enrollments", "status", ["completed", "dropped"])
    assert set(coverage.real_distinct_values) == {"completed", "dropped", "active"}
    assert coverage.matched == ["completed", "dropped"]
    assert "active" not in coverage.matched


def test_value_set_reports_invented_candidates_as_unmatched():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    coverage = _test_value_set(toolkit, "enrollments", "status", ["completed", "totally_made_up"])
    assert "totally_made_up" in coverage.unmatched_candidates
    assert coverage.coverage_ratio == 0.5


# --- aggregate -------------------------------------------------------------------


def test_aggregate_sum_matches_real_data():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    result = _aggregate(toolkit, "enrollments", "score", AggOp.MEAN)
    assert result.rows[0].value == pytest.approx(83.72, abs=0.01)


def test_aggregate_grouped_by_status():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    result = _aggregate(toolkit, "enrollments", "enrollment_id", AggOp.COUNT, group_by="status")
    groups = {r.group for r in result.rows}
    assert groups == {"completed", "dropped", "active"}


def test_aggregate_where_filter_is_a_bound_parameter_not_sql_text():
    """Injection attempt in a where value must never affect the query - it's
    bound as a literal, not interpolated."""
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    injection = "active'; DROP TABLE enrollments; --"
    result = _aggregate(toolkit, "enrollments", "enrollment_id", AggOp.COUNT, where={"status": injection})
    assert result.rows[0].value == 0  # no row matches the literal injection string - harmless
    # the table must still exist and be queryable afterwards
    sanity = _aggregate(toolkit, "enrollments", "enrollment_id", AggOp.COUNT)
    assert sanity.rows[0].value == 10


def test_aggregate_op_outside_the_enum_is_rejected_at_the_type_boundary():
    with pytest.raises(ValueError):
        AggOp("custom_formula")


def test_aggregate_rejects_unknown_column():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    with pytest.raises(AllowlistViolation):
        _aggregate(toolkit, "enrollments", "not_a_column", AggOp.SUM)


# --- sample_rows -----------------------------------------------------------------


def test_sample_rows_returns_real_rows():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    rows = _sample_rows(toolkit, "enrollments", ["enrollment_id", "score"], limit=5)
    assert len(rows.rows) == 5
    assert set(rows.rows[0]) == {"enrollment_id", "score"}


def test_sample_rows_refuses_pii_columns(bookings_csv: Path):
    toolkit = _toolkit_for(bookings_csv)
    with pytest.raises(AllowlistViolation):
        _sample_rows(toolkit, "bookings", ["customer_name"], limit=5)


def test_sample_rows_caps_the_limit():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    rows = _sample_rows(toolkit, "enrollments", ["enrollment_id"], limit=10_000)
    assert len(rows.rows) <= 15  # MAX_SAMPLE_ROWS, never the raw requested limit


def test_sample_rows_where_contains_finds_a_real_literal():
    """The within-a-table 'grep' capability - a literal the agent doesn't
    know the exact column for, resolved by search rather than a guess."""
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    rows = _sample_rows(
        toolkit, "enrollments", ["enrollment_id", "status"], limit=20, where_contains="dropped"
    )
    assert rows.rows
    assert all(r["status"] == "dropped" for r in rows.rows)


def test_sample_rows_where_contains_is_case_insensitive():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    rows = _sample_rows(
        toolkit, "enrollments", ["enrollment_id", "status"], limit=20, where_contains="DROPPED"
    )
    assert rows.rows


def test_sample_rows_where_contains_no_match_returns_empty_not_an_error():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    rows = _sample_rows(
        toolkit, "enrollments", ["enrollment_id", "status"], limit=20, where_contains="nonexistent_literal_xyz"
    )
    assert rows.rows == []


def test_sample_rows_where_contains_never_interpolates_the_literal_into_sql():
    toolkit = _toolkit_for(DATASETS_ROOT / "edtech.sqlite")
    injection = "x'; DROP TABLE enrollments; --"
    rows = _sample_rows(
        toolkit, "enrollments", ["enrollment_id", "status"], limit=5, where_contains=injection
    )
    assert rows.rows == []  # no match, and no exception - table still queryable after
    sanity = _sample_rows(toolkit, "enrollments", ["enrollment_id"], limit=1)
    assert sanity.rows


# --- LangChain wrapper surface ----------------------------------------------------


def test_build_investigation_tools_returns_exactly_six_tools():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    tools = build_investigation_tools(ds, structural)
    assert len(tools) == 6
    names = {t.name for t in tools}
    assert names == {
        "inspect_column", "compare_columns", "check_relationship",
        "test_value_set", "aggregate", "sample_rows",
    }


def test_tool_wrapper_never_raises_reports_error_string_instead():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    tools = build_investigation_tools(ds, structural)
    inspect = next(t for t in tools if t.name == "inspect_column")
    result = inspect.invoke({"table": "enrollments", "column": "does_not_exist"})
    assert "ERROR" in result
