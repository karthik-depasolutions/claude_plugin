"""Live-database ingestion via `forge_core.ingestion.postgres`. Needs a real
Postgres reachable at `FORGE_TEST_POSTGRES_URL` (or the docker-compose
`postgres` service defaults) - skips cleanly everywhere else, including
plain local `pytest` runs without Docker. CI provides the service; see
.github/workflows/ci.yml."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest
from forge_core.ingestion.postgres import CREDENTIAL_ENV_VAR, PostgresAdapter, extract_schema, redact
from forge_core.ingestion.registry import default_run_id, ingest, prepare_source_for_persistence
from forge_core.models.common import SourceKind

TEST_POSTGRES_URL = os.environ.get(
    "FORGE_TEST_POSTGRES_URL", "postgresql://forge:forge@localhost:5432/forge"
)


def _postgres_reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1.5):
            return True
    except OSError:
        return False


requires_live_postgres = pytest.mark.skipif(
    not _postgres_reachable(TEST_POSTGRES_URL),
    reason=f"no Postgres reachable at {redact(TEST_POSTGRES_URL)} - start one to run this test",
)


def test_supports_matches_only_postgres_schemes():
    adapter = PostgresAdapter()
    assert adapter.supports("postgresql://user:pw@host/db")
    assert adapter.supports("postgres://user:pw@host/db")
    assert not adapter.supports("mysql://user:pw@host/db")
    assert not adapter.supports("/some/file/path.csv")
    assert not adapter.supports(r"D:\data\bookings.csv")


def test_redact_strips_the_password_but_keeps_everything_else_useful():
    redacted = redact("postgresql://forge:s3cr3t@localhost:5432/forge")
    assert "s3cr3t" not in redacted
    assert "forge:***@localhost:5432/forge" in redacted


def test_redact_is_a_no_op_for_plain_paths():
    assert redact(r"D:\data\bookings.csv") == r"D:\data\bookings.csv"


def test_extract_schema_defaults_to_public_when_no_options_present():
    assert extract_schema("postgresql://forge:forge@localhost:5432/forge") == "public"


def test_extract_schema_reads_the_search_path_option():
    url = "postgresql://u:p@host:5432/db?options=-csearch_path%3Dclient_run123"
    assert extract_schema(url) == "client_run123"


def test_extract_schema_takes_the_first_schema_in_a_comma_separated_search_path():
    url = "postgresql://u:p@host:5432/db?options=-csearch_path%3Dclient_run123,public"
    assert extract_schema(url) == "client_run123"


def test_extract_schema_ignores_unrelated_options():
    url = "postgresql://u:p@host:5432/db?options=-cstatement_timeout%3D5000"
    assert extract_schema(url) == "public"


def test_prepare_source_for_persistence_never_returns_the_raw_credential(tmp_path):
    connection_string = "postgresql://forge:s3cr3t@localhost:5432/forge"
    stored = prepare_source_for_persistence(connection_string)

    assert stored == f"${{{CREDENTIAL_ENV_VAR}}}"
    assert "s3cr3t" not in stored
    assert os.environ[CREDENTIAL_ENV_VAR] == connection_string

    # A plain path still resolves to an absolute path exactly as before.
    a_file = tmp_path / "data.csv"
    a_file.write_text("a,b\n1,2\n", encoding="utf-8")
    assert prepare_source_for_persistence(str(a_file)) == str(a_file.resolve())


def test_default_run_id_is_stable_and_never_contains_the_credential():
    connection_string = "postgresql://forge:s3cr3t@localhost:5432/forge"
    run_id = default_run_id(connection_string)
    assert "s3cr3t" not in run_id
    assert run_id == default_run_id(connection_string)  # stable across calls
    assert run_id.startswith("live-db-")


@requires_live_postgres
def test_ingest_connects_and_describes_every_table_in_the_public_schema():
    ds = ingest(TEST_POSTGRES_URL)
    assert ds.kind == SourceKind.POSTGRES
    assert ds.tables, "expected at least one table in the 'public' schema of the test database"
    # No file was copied anywhere - a live source has nothing to bundle.
    assert ds.connection.original_paths == []
    assert ds.connection.credential_env_vars == [CREDENTIAL_ENV_VAR]


@requires_live_postgres
def test_ingest_via_the_persisted_placeholder_reconnects_using_the_stashed_env_var():
    placeholder = prepare_source_for_persistence(TEST_POSTGRES_URL)
    ds = ingest(placeholder)
    assert ds.kind == SourceKind.POSTGRES


@requires_live_postgres
def test_ingest_scans_an_explicit_non_public_schema_and_fully_qualifies_physical_refs():
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{TEST_POSTGRES_URL}' AS srcdb (TYPE POSTGRES)")
    con.execute("CREATE SCHEMA IF NOT EXISTS srcdb.forge_test_schema")
    con.execute("DROP TABLE IF EXISTS srcdb.forge_test_schema.widgets")
    con.execute("CREATE TABLE srcdb.forge_test_schema.widgets (id INT, name TEXT)")
    con.execute("INSERT INTO srcdb.forge_test_schema.widgets VALUES (1, 'a'), (2, 'b')")
    con.close()

    try:
        ds = PostgresAdapter().ingest(TEST_POSTGRES_URL, schema="forge_test_schema")
        assert [t.name for t in ds.tables] == ["widgets"]
        assert ds.tables[0].physical_ref == 'srcdb."forge_test_schema"."widgets"'
        assert ds.tables[0].row_count == 2
    finally:
        con = duckdb.connect(":memory:")
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{TEST_POSTGRES_URL}' AS srcdb (TYPE POSTGRES)")
        con.execute("DROP TABLE IF EXISTS srcdb.forge_test_schema.widgets")
        con.execute("DROP SCHEMA IF EXISTS srcdb.forge_test_schema")
        con.close()


@requires_live_postgres
def test_ingest_auto_detects_schema_from_the_search_path_option():
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{TEST_POSTGRES_URL}' AS srcdb (TYPE POSTGRES)")
    con.execute("CREATE SCHEMA IF NOT EXISTS srcdb.forge_test_autodetect")
    con.execute("DROP TABLE IF EXISTS srcdb.forge_test_autodetect.gadgets")
    con.execute("CREATE TABLE srcdb.forge_test_autodetect.gadgets (id INT)")
    con.execute("INSERT INTO srcdb.forge_test_autodetect.gadgets VALUES (1)")
    con.close()

    scoped_url = f"{TEST_POSTGRES_URL}?options=-csearch_path%3Dforge_test_autodetect"
    try:
        ds = PostgresAdapter().ingest(scoped_url)
        assert [t.name for t in ds.tables] == ["gadgets"]
        assert ds.tables[0].physical_ref == 'srcdb."forge_test_autodetect"."gadgets"'
    finally:
        con = duckdb.connect(":memory:")
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{TEST_POSTGRES_URL}' AS srcdb (TYPE POSTGRES)")
        con.execute("DROP TABLE IF EXISTS srcdb.forge_test_autodetect.gadgets")
        con.execute("DROP SCHEMA IF EXISTS srcdb.forge_test_autodetect")
        con.close()
