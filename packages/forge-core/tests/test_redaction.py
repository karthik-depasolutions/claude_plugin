"""Denied columns (any role category a pack's guardrails deny, e.g.
`free_text`) must never reach the plugin's `data/` folder on disk - not
just be excluded from compiled SQL. Covers every source shape the
ingestion layer supports: a single file, a directory of files (denied
column on a non-fact table), and a SQLite database.
"""

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
    # resolve_bindings' own denied_columns is scoped to - a free-text column
    # on it must still show up here.
    assert "full_name" in denied.get("customers", set())


def test_write_redacted_data_files_drops_denied_columns_from_a_multi_table_source(tmp_path: Path):
    profile = _profile_for(FIXTURES_ROOT / "retail_orders")
    pack = load_pack(PACKS_ROOT / "retail-ecommerce")

    write_redacted_data_files(profile.source, profile, pack, tmp_path)

    customers_columns = (tmp_path / "data" / "customers.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "full_name" not in customers_columns
    assert "customer_id" in customers_columns  # non-denied columns must survive

    orders_header = (tmp_path / "data" / "orders.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "order_id" in orders_header.split(",")


def test_write_redacted_data_files_round_trips_a_sqlite_file(tmp_path: Path):
    profile = _profile_for(FIXTURES_ROOT / "edtech.sqlite")
    pack = load_pack(PACKS_ROOT / "edtech")

    write_redacted_data_files(profile.source, profile, pack, tmp_path)

    redacted_db = tmp_path / "data" / "edtech.sqlite"
    assert redacted_db.is_file()

    con = duckdb.connect(":memory:")
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{redacted_db.as_posix()}' AS check_db (TYPE SQLITE, READ_ONLY)")
    student_columns = [c[0] for c in con.execute('DESCRIBE check_db."students"').fetchall()]
    assert "student_id" in student_columns

    row_count = con.execute('SELECT COUNT(*) FROM check_db."enrollments"').fetchone()
    assert row_count is not None and row_count[0] > 0
    con.close()


def test_write_redacted_data_files_handles_the_plain_single_file_case(tmp_path: Path):
    profile = _profile_for(FIXTURES_ROOT / "bookings.csv")
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")

    write_redacted_data_files(profile.source, profile, pack, tmp_path)

    header = (tmp_path / "data" / "bookings.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "booking_id" in header


class _FlakyCon:
    """Forwards to a real DuckDB connection but makes the first `fail_times`
    COPY statements raise a Windows-style sharing violation."""

    def __init__(self, real: duckdb.DuckDBPyConnection, fail_times: int) -> None:
        self._real = real
        self._left = fail_times
        self.copy_calls = 0

    def execute(self, sql: str, *args, **kwargs):
        if sql.lstrip().startswith("COPY"):
            self.copy_calls += 1
            if self._left > 0:
                self._left -= 1
                raise duckdb.IOException("Cannot open file: used by another process")
        return self._real.execute(sql, *args, **kwargs)


def test_export_table_retries_a_transient_sharing_violation(tmp_path: Path, monkeypatch):
    from forge_core.packaging import redaction

    monkeypatch.setattr(redaction, "_WRITE_BACKOFF_S", 0.001)
    real = duckdb.connect(":memory:")
    real.execute("CREATE TABLE t AS SELECT 1 AS a")
    con = _FlakyCon(real, fail_times=2)
    dest = tmp_path / "out.csv"

    redaction._export_table(con, "SELECT * FROM t", ".csv", dest)

    assert con.copy_calls == 3  # 2 failures + 1 success
    assert dest.read_text(encoding="utf-8").splitlines()[0] == "a"


def test_export_table_gives_up_after_the_retry_budget(tmp_path: Path, monkeypatch):
    from forge_core.packaging import redaction

    monkeypatch.setattr(redaction, "_WRITE_BACKOFF_S", 0.001)
    real = duckdb.connect(":memory:")
    real.execute("CREATE TABLE t AS SELECT 1 AS a")
    con = _FlakyCon(real, fail_times=99)

    with pytest.raises(duckdb.IOException):
        redaction._export_table(con, "SELECT * FROM t", ".csv", tmp_path / "out.csv")
    assert con.copy_calls == redaction._WRITE_ATTEMPTS


if __name__ == "__main__":
    pytest.main([__file__])
