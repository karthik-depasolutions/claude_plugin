"""Dates stored as non-ISO text must not fail the build.

DuckDB's `CAST('02-05-1993' AS TIMESTAMP)` *raises* rather than returning
NULL. Every trend KPI in every pack casts its date column, so one DD-MM-YYYY
column - the norm in Indian and European exports - took an entire build down
at dry-run with a SQL error, after the full agent spend.

The fix detects the real format from the values and carries a
`STRPTIME(col, fmt)` expression through binding into SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.ingestion.registry import ingest
from forge_core.models.common import ColumnRole
from forge_core.models.schema_profile import SchemaProfile, temporal_sql_expression
from forge_core.profiling import build_structural_only
from forge_core.runtime_session import open_session

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"

ROWS = [
    (1, "Sales", "02-05-1993", 60000, "active"),
    (2, "Sales", "15-08-1994", 72000, "active"),
    (3, "Ops", "01-01-1995", 55000, "left"),
    (4, "Ops", "20-11-1996", 81000, "active"),
    (5, "Tech", "05-05-1997", 64000, "left"),
    (6, "Tech", "31-12-1999", 90000, "active"),
]


@pytest.fixture
def hr_db(tmp_path: Path) -> Path:
    """SQLite stores these as TEXT, exactly like the customer database that
    hit this. A CSV would not reproduce it - DuckDB's CSV sniffer parses
    DD-MM-YYYY into a real DATE on the way in."""
    path = tmp_path / "hr.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE employees (emp_id INTEGER, dept TEXT, from_date TEXT, "
        "salary INTEGER, status TEXT)"
    )
    con.executemany("INSERT INTO employees VALUES (?,?,?,?,?)", ROWS)
    con.commit()
    con.close()
    return path


def _profile(path: Path):
    data_source = ingest(path)
    structural = build_structural_only(data_source)
    return data_source, structural


def test_a_text_column_of_day_first_dates_is_recognised_as_a_date(hr_db):
    _ds, structural = _profile(hr_db)
    column = next(c for c in structural.columns_for("employees") if c.name == "from_date")

    assert column.guessed_role == ColumnRole.DATE
    assert column.temporal_format == "%d-%m-%Y"


def test_the_format_is_read_from_values_not_the_column_name(hr_db, tmp_path: Path):
    """`from_date` and a column called `zzz` must be classified identically -
    the values decide, as everywhere else in profiling."""
    path = tmp_path / "renamed.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (a INTEGER, zzz TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?)", [(i, r[2]) for i, r in enumerate(ROWS)])
    con.commit()
    con.close()

    _ds, structural = _profile(path)
    column = next(c for c in structural.columns_for("t") if c.name == "zzz")
    assert column.guessed_role == ColumnRole.DATE
    assert column.temporal_format == "%d-%m-%Y"


def test_day_first_beats_month_first_when_the_data_proves_it(hr_db):
    """"31-12-1999" can only be day-first; "02-05-1993" alone is ambiguous.
    One unambiguous row settles the whole column."""
    _ds, structural = _profile(hr_db)
    column = next(c for c in structural.columns_for("employees") if c.name == "from_date")
    assert column.temporal_format == "%d-%m-%Y"


def test_iso_dates_still_need_no_expression(tmp_path: Path):
    """The common case must stay on the cheap path - a plain column
    reference, no STRPTIME wrapper."""
    path = tmp_path / "iso.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (a INTEGER, d TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?)", [(1, "1993-05-02"), (2, "1999-12-31")])
    con.commit()
    con.close()

    _ds, structural = _profile(path)
    column = next(c for c in structural.columns_for("t") if c.name == "d")
    assert column.guessed_role == ColumnRole.DATE
    assert column.temporal_format is None


def test_the_trend_kpi_compiles_and_actually_runs(hr_db):
    """The end-to-end regression: this exact KPI raised
    `Conversion Error: invalid timestamp field format: "02-05-1993"` at
    dry-run and failed the whole build."""
    data_source, structural = _profile(hr_db)
    profile = SchemaProfile(
        data_source_id=data_source.id, structural=structural, semantic=None, source=data_source
    )
    pack = load_pack(PACKS_ROOT / "generic-analytics")
    bindings = resolve_bindings(
        profile,
        pack,
        overrides={"date_dim": "from_date", "measure_amount": "salary", "category_dim": "dept"},
    )

    date_binding = bindings.column("date_dim")
    assert date_binding.sql_expression == 'STRPTIME("from_date", \'%d-%m-%Y\')'

    kpi_defs = compile_all(pack, bindings)
    trend = next(k for k in kpi_defs.kpis if k.id == "trend_by_month")
    assert "STRPTIME" in trend.sql

    con = open_session(data_source)
    try:
        rows = con.execute(trend.sql).fetchall()
    finally:
        con.close()

    months = {r[0] for r in rows}
    assert "1993-05" in months, "02-05-1993 must bucket as May 1993, not February"
    assert "1999-12" in months


def test_generated_metrics_carry_the_format_to_the_runtime(hr_db):
    """The shipped MCP runtime builds its own time-bucket SQL from
    `metric_defs.json`, so the format has to travel with the metric or a
    metric that validated at build time raises at query time."""
    from forge_core.compiler.metric_generator import generate_metrics

    _ds, structural = _profile(hr_db)
    metrics = generate_metrics("employees", structural, set())
    timed = [m for m in metrics if m.time_column == "from_date"]

    assert timed, "expected at least one metric bucketed on the date column"
    assert all(m.time_format == "%d-%m-%Y" for m in timed)


@pytest.mark.parametrize(
    ("column", "fmt", "expected"),
    [
        ("d", None, '"d"'),
        ("d", "%d-%m-%Y", "STRPTIME(\"d\", '%d-%m-%Y')"),
    ],
)
def test_expression_helper(column, fmt, expected):
    assert temporal_sql_expression(column, fmt) == expected


def test_the_runtime_helper_matches_the_build_time_one():
    """The runtime ships standalone and cannot import forge_core, so the
    helper is duplicated. If the two drift, a metric works in one surface
    and raises in the other."""
    from mis_mcp_runtime.engine.metric_query import (
        temporal_sql_expression as runtime_expression,
    )

    for fmt in (None, "%d-%m-%Y", "%m/%d/%Y %H:%M:%S"):
        assert runtime_expression("d", fmt, qualifier="t") == temporal_sql_expression(
            "d", fmt, qualifier="t"
        )
