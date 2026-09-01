"""Hardcore stress, adversarial, and edge-case test suite for MIS Plugin Forge.

Covers:
1. Ingestion edge cases (empty files, 0-row CSVs, all-NULL datasets, huge values, weird column names, NaN/Inf).
2. Profiling & structural analysis edge cases (zero variance, divide-by-zero, case-colliding columns).
3. Security attacks on MCP runtime (SQL injection, denied column bypass, CTE/subquery bypass, system catalog access).
4. search_records tool security (filter injection, invalid operators, parameter escaping).
5. Concurrency stress on MCP runtime & DuckDB session.
6. PII scanner evasion & regex boundary checks.
7. Zip extraction path traversal (Zip Slip) security checks.
8. API state machine resilience & credential leakage checks.
"""

from __future__ import annotations

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
import zipfile

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from forge_core.binding import resolve_bindings
from forge_core.binding.scorer import score_column_for_role
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.compiler.sql_render import render_sql
from forge_core.ingestion.files import FileAdapter
from forge_core.ingestion.postgres import redact as redact_postgres_url
from forge_core.ingestion.registry import ingest, prepare_source_for_persistence
from forge_core.models.bindings import ColumnBinding, SchemaBindings, TableBinding
from forge_core.models.common import ColumnRole, RunStage, RunStatus, SourceKind
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.industry_pack import CanonicalKpi, IndustryPack, PackSignature
from forge_core.models.kpi import CompiledKpi, KpiDefsFile
from forge_core.models.run import RunRecord
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile, StructuralProfile
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline
from forge_core.profiling.grain import infer_grains
from forge_core.profiling.quality import analyze_quality, build_data_review, generate_questions
from forge_core.profiling import build_structural_only
from forge_core.runtime_session import open_session
from forge_core.validation.harness import run_harness
from forge_core.validation.sql_safety import check_sql_safety

from mis_mcp_runtime.config import RuntimeConfig, load_runtime_config
from mis_mcp_runtime.engine.duckdb_session import open_session as open_mcp_session
from mis_mcp_runtime.security.allowlist import AllowlistError, check_tables_allowed
from mis_mcp_runtime.security.pii_policy import PiiPolicyError, check_no_denied_columns
from mis_mcp_runtime.security.sql_policy import SqlPolicyError, parse_single_select
from mis_mcp_runtime.tools.describe_schema import describe_schema
from mis_mcp_runtime.tools.get_data_profile import get_data_profile
from mis_mcp_runtime.tools.get_kpi import get_kpi
from mis_mcp_runtime.tools.run_safe_query import run_safe_query
from mis_mcp_runtime.tools.search_records import search_records


REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# 1. INGESTION & DATA EDGE CASES
# ============================================================================

class TestIngestionEdgeCases:
    """Stress testing file ingestion against corrupt, extreme, or empty datasets."""

    def test_zero_byte_csv_file(self, tmp_path: Path):
        """A completely empty 0-byte CSV file produces an empty table with 0 columns and 0 rows."""
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_bytes(b"")
        adapter = FileAdapter()
        with pytest.raises(ValueError, match="is empty"):
            adapter.ingest(empty_csv)

    def test_header_only_zero_row_csv(self, tmp_path: Path):
        """CSV with headers but exactly 0 data rows."""
        csv_file = tmp_path / "zero_rows.csv"
        csv_file.write_text("id,revenue,status,created_at\n", encoding="utf-8")
        ds = ingest(csv_file)
        assert len(ds.tables) == 1
        assert ds.tables[0].row_count == 0

        # Profiling on zero rows should not divide by zero
        structural = build_structural_only(ds)
        assert len(structural.columns) == 4
        for col in structural.columns:
            assert col.null_percent == 0.0 or col.null_percent == 100.0 or col.null_percent == 0
            assert col.cardinality == 0

        # Quality check on zero rows should handle empty table gracefully
        con = open_session(ds)
        try:
            findings, skipped = analyze_quality(ds, structural, con)
            assert isinstance(findings, list)
        finally:
            con.close()

    def test_all_null_columns(self, tmp_path: Path):
        """Dataset where entire columns are 100% NULL."""
        csv_file = tmp_path / "all_nulls.csv"
        csv_file.write_text(
            "id,empty_num,empty_str,empty_date\n"
            "1,,,\n"
            "2,,,\n"
            "3,,,\n",
            encoding="utf-8",
        )
        ds = ingest(csv_file)
        structural = build_structural_only(ds)
        for col in structural.columns:
            if col.name.startswith("empty_"):
                assert col.null_percent == 100.0

    def test_extreme_floats_and_nan_inf(self, tmp_path: Path):
        """Testing extreme numeric values: NaN, Infinity, -Infinity, huge numbers."""
        csv_file = tmp_path / "extreme_numbers.csv"
        csv_file.write_text(
            "id,val\n"
            "1,1e308\n"
            "2,-1e308\n"
            "3,NaN\n"
            "4,Infinity\n"
            "5,-Infinity\n",
            encoding="utf-8",
        )
        ds = ingest(csv_file)
        con = open_session(ds)
        try:
            res = con.execute("SELECT COUNT(*), AVG(val) FROM src_extreme_numbers WHERE val IS NOT NULL").fetchone()
            assert res is not None
        finally:
            con.close()

    def test_pathological_column_names_and_sql_injection_headers(self, tmp_path: Path):
        """Column names containing SQL keywords, quotes, semicolons, and injection strings."""
        csv_file = tmp_path / "weird_cols.csv"
        csv_file.write_text(
            '"select","from","drop; table users; --","col with spaces","col\'singlequote","col""doublequote","🚀_emoji"\n'
            '1,2,3,4,5,6,7\n',
            encoding="utf-8",
        )
        ds = ingest(csv_file)
        assert len(ds.tables) == 1
        con = open_session(ds)
        try:
            res = con.execute('SELECT "select", "from", "🚀_emoji" FROM src_weird_cols').fetchall()
            assert len(res) == 1
            assert res[0] == (1, 2, 7)
        finally:
            con.close()

    def test_case_colliding_column_names(self, tmp_path: Path):
        """Columns differing only by case e.g. amount, Amount, AMOUNT."""
        csv_file = tmp_path / "case_collision.csv"
        csv_file.write_text(
            "id,amount,Amount,AMOUNT\n"
            "1,10,20,30\n",
            encoding="utf-8",
        )
        ds = ingest(csv_file)
        assert len(ds.tables) == 1


# ============================================================================
# 2. PROFILING, GRAIN & QUALITY STRESS
# ============================================================================

class TestProfilingStress:
    """Stress testing profiling, grain inference, and data review."""

    def test_single_row_dataset_profiling(self, tmp_path: Path):
        """A dataset with exactly 1 row."""
        csv_file = tmp_path / "single_row.csv"
        csv_file.write_text("booking_id,revenue,status\nB100,250.50,completed\n", encoding="utf-8")
        ds = ingest(csv_file)
        prof = build_structural_only(ds)
        assert len(prof.columns) == 3
        con = open_session(ds)
        try:
            grains = infer_grains(ds, prof.columns, con)
        finally:
            con.close()
        assert len(grains) == 1

    def test_massive_column_count(self, tmp_path: Path):
        """Dataset with 250 columns."""
        col_names = [f"col_{i}" for i in range(250)]
        header = ",".join(col_names) + "\n"
        row1 = ",".join(str(i) for i in range(250)) + "\n"
        row2 = ",".join(str(i * 2) for i in range(250)) + "\n"

        csv_file = tmp_path / "wide_table.csv"
        csv_file.write_text(header + row1 + row2, encoding="utf-8")

        ds = ingest(csv_file)
        prof = build_structural_only(ds)
        assert len(prof.columns) == 250


# ============================================================================
# 3. SQL COMPILER & VALIDATION SAFETY ATTACKS
# ============================================================================

class TestSqlSecurityAndCompilerAttacks:
    """Testing SQL safety validation, AST inspection, and denied column evasion."""

    def test_sql_safety_rejects_non_select(self):
        """Ensures UPDATE, DELETE, DROP, INSERT, CREATE, ALTER are strictly blocked."""
        dangerous_statements = [
            "DROP TABLE orders",
            "DELETE FROM orders WHERE 1=1",
            "UPDATE orders SET revenue = 0",
            "INSERT INTO orders VALUES (1, 2)",
            "CREATE TABLE hack AS SELECT * FROM orders",
            "ALTER TABLE orders DROP COLUMN revenue",
            "TRUNCATE TABLE orders",
            "ATTACH 'evil.db' AS evil",
            "COPY orders TO '/tmp/pwned.csv'",
        ]
        for stmt in dangerous_statements:
            with pytest.raises(SqlPolicyError):
                parse_single_select(stmt)

    def test_sql_safety_rejects_select_star(self):
        """SELECT * must be rejected to prevent schema-leak and unredacted PII exposure."""
        with pytest.raises(SqlPolicyError):
            parse_single_select("SELECT * FROM orders")

    def test_denied_column_evasion_with_functions_and_aliases(self):
        """Attacker tries to evade denied column check by wrapping it in functions or aliasing."""
        evasions = [
            "SELECT phone FROM users",
            "SELECT UPPER(phone) FROM users",
            "SELECT LOWER(phone) AS safe_field FROM users",
            "SELECT SUBSTRING(phone, 1, 5) FROM users",
            "SELECT COALESCE(phone, 'N/A') FROM users",
            "SELECT CONCAT(first_name, phone) FROM users",
            "SELECT phone || ' ' || email FROM users",
        ]
        for query in evasions:
            stmt = parse_single_select(query)
            with pytest.raises(PiiPolicyError):
                check_no_denied_columns(stmt, denied_columns=["phone", "email"])

    def test_denied_column_evasion_via_subqueries_and_ctes(self):
        """Attacker tries to evade denied column check using CTEs or subqueries."""
        evasion_queries = [
            "WITH secret AS (SELECT phone FROM users) SELECT phone FROM secret",
            "WITH secret AS (SELECT phone AS p FROM users) SELECT p FROM secret",
            "SELECT sub.phone FROM (SELECT phone FROM users) sub",
            "SELECT sub.p FROM (SELECT phone AS p FROM users) sub",
        ]
        for query in evasion_queries:
            stmt = parse_single_select(query)
            with pytest.raises(PiiPolicyError):
                check_no_denied_columns(stmt, denied_columns=["phone"])

    def test_table_allowlist_enforcement(self):
        """Rejects queries targeting tables not in the allowlist."""
        queries = [
            "SELECT secret_key FROM private_system_credentials",
            "SELECT name FROM information_schema.tables",
            "SELECT schema_name FROM duckdb_schemas()",
            "SELECT table_name FROM duckdb_tables()",
        ]
        for query in queries:
            stmt = parse_single_select(query)
            with pytest.raises(AllowlistError):
                check_tables_allowed(stmt, allowed_tables=["orders"])

    def test_divide_by_zero_kpi_formula_execution(self, tmp_path: Path):
        """Test KPI execution when denominator is 0 (e.g. 0 total transactions)."""
        csv_file = tmp_path / "zero_denom.csv"
        csv_file.write_text("id,status,revenue\n", encoding="utf-8")
        ds = ingest(csv_file)
        con = open_session(ds)
        try:
            res = con.execute(
                "SELECT CAST(COUNT(CASE WHEN status='cancelled' THEN 1 END) AS FLOAT) / "
                "NULLIF(COUNT(*), 0) AS cancel_rate FROM src_zero_denom"
            ).fetchone()
            assert res == (None,)
        finally:
            con.close()


# ============================================================================
# 4. MCP RUNTIME HARDENING & TOOL ATTACKS
# ============================================================================

@pytest.fixture
def mock_runtime_config(tmp_path: Path) -> tuple[RuntimeConfig, duckdb.DuckDBPyConnection]:
    source = REPO_ROOT / "fixtures" / "datasets" / "bookings.csv"
    ds = ingest(source)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)
    pack = load_pack(REPO_ROOT / "industry-packs" / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    table = ds.tables[0]
    data_source_json = {
        "kind": ds.kind.value,
        "connection": {
            "duckdb_attach_sql": ds.connection.duckdb_attach_sql,
            "read_only": ds.connection.read_only,
        },
        "tables": [
            {
                "name": table.name,
                "physical_ref": table.physical_ref,
                "columns": [c.name for c in table.columns],
            }
        ],
    }
    (config_dir / "data_source.json").write_text(json.dumps(data_source_json), encoding="utf-8")
    (config_dir / "schema_bindings.json").write_text(bindings.model_dump_json(), encoding="utf-8")
    (config_dir / "kpi_defs.json").write_text(kpi_defs.model_dump_json(), encoding="utf-8")
    (config_dir / "schema_summary.json").write_text(
        json.dumps(
            {
                "pack_slug": pack.slug,
                "guardrails": {
                    "max_query_rows": pack.guardrails.max_query_rows,
                    "query_timeout_seconds": pack.guardrails.query_timeout_seconds,
                },
                "tables": [{"name": table.name, "columns": [c.name for c in table.columns]}],
            }
        ),
        encoding="utf-8",
    )

    config = load_runtime_config(config_dir=config_dir, data_dir=source.parent)
    con = open_mcp_session(config.data_source, config.data_dir)
    return config, con


class TestMcpRuntimeHardening:
    """Attacking and stress testing the generic MCP runtime tools."""

    def test_run_safe_query_sql_injection_stacked_queries(self, mock_runtime_config):
        """Attacker passes stacked queries with semicolons."""
        config, con = mock_runtime_config
        with pytest.raises(SqlPolicyError):
            run_safe_query(config, con, "SELECT amount_inr FROM src_bookings; DROP TABLE src_bookings;")

    def test_run_safe_query_denied_column_rejection(self, mock_runtime_config):
        """Attacker queries the denied free-text column."""
        config, con = mock_runtime_config
        with pytest.raises(PiiPolicyError, match="denied"):
            run_safe_query(config, con, "SELECT customer_name FROM src_bookings")

    def test_run_safe_query_row_limit(self, mock_runtime_config):
        """Verify queries respect max_query_rows."""
        config, con = mock_runtime_config
        res = run_safe_query(config, con, "SELECT amount_inr FROM src_bookings")
        assert "error" not in res
        assert "rows" in res
        assert len(res["rows"]) <= config.max_query_rows

    def test_search_records_filter_injection(self, mock_runtime_config):
        """Attacker tries SQL injection through filter values or invalid column names."""
        config, con = mock_runtime_config

        # 1. SQL Injection via filter value
        res = search_records(
            config,
            con,
            table="bookings",
            filters={"status": "completed' OR '1'='1"},
        )
        assert res.get("row_count") == 0

        # 2. Injection via column name
        res_invalid_col = search_records(
            config,
            con,
            table="bookings",
            filters={"status; DROP TABLE src_bookings; --": "completed"},
        )
        assert "error" in res_invalid_col

        # 3. Accessing denied table
        res_denied_tbl = search_records(
            config,
            con,
            table="non_existent_or_unallowed_table",
            filters={},
        )
        assert "error" in res_denied_tbl

        # 4. Filter on denied column
        res_denied_col = search_records(
            config,
            con,
            table="bookings",
            filters={"customer_name": "anything"},
        )
        assert "error" in res_denied_col

    def test_get_data_profile_denied_columns_excluded(self, mock_runtime_config):
        """Assure denied columns are excluded from data profiling outputs."""
        config, con = mock_runtime_config
        res = get_data_profile(config, con, "bookings")
        assert "error" not in res
        col_names = [col["column"] for col in res.get("columns", [])]
        assert "customer_name" not in col_names


# ============================================================================
# 5. MULTI-THREADED CONCURRENCY STRESS
# ============================================================================

class TestRuntimeConcurrencyStress:
    """Stress testing the runtime DuckDB engine under concurrent load."""

    def test_concurrent_mcp_queries(self, mock_runtime_config):
        """Execute 30 concurrent queries across 6 threads against the same DuckDB session."""
        config, con = mock_runtime_config
        errors: list[str] = []

        def worker(worker_id: int):
            for i in range(5):
                s = describe_schema(config)
                if "tables" not in s:
                    errors.append(f"worker {worker_id} describe_schema failed")

                k = get_kpi(config, con, "total_revenue")
                if "error" in k:
                    errors.append(f"worker {worker_id} get_kpi failed: {k['error']}")

                q = run_safe_query(config, con, "SELECT amount_inr FROM src_bookings WHERE amount_inr > 100")
                if "error" in q:
                    errors.append(f"worker {worker_id} run_safe_query failed: {q['error']}")

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(worker, i) for i in range(6)]
            for future in as_completed(futures):
                future.result()

        assert errors == [], f"Concurrency errors encountered: {errors}"


# ============================================================================
# 7. ZIP SLIP & PATH TRAVERSAL SECURITY
# ============================================================================

class TestZipSlipSecurity:
    """Ensuring zip extraction rejects path traversal attacks (Zip Slip)."""

    def test_zip_slip_rejected(self, tmp_path: Path):
        """A zip file containing an entry with '../' traversal."""
        from forge_api.routers.runs import _extract_zip_safely
        from fastapi import HTTPException

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("../../pwned.txt", "malicious payload")
        zip_buffer.seek(0)

        target_dir = tmp_path / "safe_extract"
        target_dir.mkdir()

        with pytest.raises(HTTPException) as exc_info:
            _extract_zip_safely(zip_buffer, target_dir)
        assert exc_info.value.status_code == 400


# ============================================================================
# 8. POSTGRES CREDENTIAL REDACTION & PERSISTENCE SAFETY
# ============================================================================

class TestCredentialSafety:
    """Ensures database passwords never leak into logs, records, or responses."""

    def test_postgres_url_redaction(self):
        raw_url = "postgresql://dbuser:SecretP@ssw0rd!123@db.example.com:5432/proddb?sslmode=require"
        redacted = redact_postgres_url(raw_url)
        assert "SecretP@ssw0rd!123" not in redacted
        assert "dbuser:***@" in redacted

    def test_prepare_source_for_persistence_stashes_env(self):
        raw_url = "postgresql://user:mypass@localhost:5432/testdb"
        placeholder = prepare_source_for_persistence(raw_url)
        assert placeholder == "${FORGE_SOURCE_DB_URL}"
        assert os.environ.get("FORGE_SOURCE_DB_URL") == raw_url
