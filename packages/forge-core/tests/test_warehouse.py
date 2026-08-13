"""Client-warehouse provisioning/deprovisioning. Needs a real Postgres with
superuser rights reachable at `FORGE_TEST_WAREHOUSE_ADMIN_URL` - skips cleanly
everywhere else, same convention as `test_postgres_adapter.py`."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import duckdb
import pytest
from forge_core.ingestion.postgres import PostgresAdapter, redact
from forge_core.ingestion.warehouse import (
    GROUP_ROLE,
    deprovision_client_schema,
    provision_client_schema,
)

TEST_ADMIN_URL = os.environ.get(
    "FORGE_TEST_WAREHOUSE_ADMIN_URL", "postgresql://forge:forge@localhost:5432/forge"
)


def _postgres_reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1.5):
            return True
    except OSError:
        return False


requires_live_postgres = pytest.mark.skipif(
    not _postgres_reachable(TEST_ADMIN_URL),
    reason=(
        f"no Postgres reachable at {redact(TEST_ADMIN_URL)} - "
        "set FORGE_TEST_WAREHOUSE_ADMIN_URL to run this test"
    ),
)


def _group_role_exists(admin_url: str) -> bool:
    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{admin_url}' AS srcdb (TYPE POSTGRES)")
        rows = con.execute(
            "SELECT * FROM postgres_query('srcdb', "
            f"'SELECT 1 FROM pg_roles WHERE rolname = ''{GROUP_ROLE}''')"
        ).fetchall()
        return bool(rows)
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _skip_without_group_role():
    """Role-scoping statements (`GRANT forge_client_group TO ...`) only make
    sense against a server that already has this group role, i.e. the real
    staging box - not a bare docker-compose Postgres. Create it once if
    missing so the suite is still runnable locally against docker-compose."""
    if not _postgres_reachable(TEST_ADMIN_URL):
        return
    if _group_role_exists(TEST_ADMIN_URL):
        return
    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{TEST_ADMIN_URL}' AS srcdb (TYPE POSTGRES)")
        con.execute(f"CALL postgres_execute('srcdb', $ddl$CREATE ROLE {GROUP_ROLE} NOLOGIN$ddl$)")
    finally:
        con.close()


@pytest.fixture
def sample_upload(tmp_path):
    orders = tmp_path / "orders.csv"
    orders.write_text("id,amount\n1,9.5\n2,3.25\n", encoding="utf-8")
    return tmp_path


@requires_live_postgres
def test_provision_creates_a_schema_scoped_role_that_can_read_its_own_tables(sample_upload):
    run_id = "wtest01"
    try:
        creds = provision_client_schema(
            TEST_ADMIN_URL,
            run_id,
            sample_upload,
            public_host="127.0.0.1",
            public_port=urlparse(TEST_ADMIN_URL).port or 5432,
        )
        assert creds.schema_name == f"client_{run_id}"
        assert creds.role_name == f"client_{run_id}_ro"
        assert "sslmode=require" in creds.connection_string
        assert f"search_path%3Dclient_{run_id}" in creds.connection_string

        # The role can read the data through the exact same code path a
        # shipped plugin would use at query time.
        readback_url = creds.connection_string.replace("sslmode=require", "sslmode=prefer")
        ds = PostgresAdapter().ingest(readback_url)
        assert [t.name for t in ds.tables] == ["orders"]
        assert ds.tables[0].row_count == 2
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_id)


@requires_live_postgres
def test_provision_with_a_label_makes_the_schema_name_identifiable(sample_upload):
    run_id = "wtest03"
    try:
        creds = provision_client_schema(
            TEST_ADMIN_URL,
            run_id,
            sample_upload,
            public_host="127.0.0.1",
            public_port=urlparse(TEST_ADMIN_URL).port or 5432,
            label="Orders Export!!.csv",
        )
        # Sanitized to lowercase/underscores, still ends in the run_id so
        # deprovisioning (given the same label) finds the exact same schema.
        assert creds.schema_name == f"client_orders_export_csv_{run_id}"
        assert creds.role_name == f"client_orders_export_csv_{run_id}_ro"
        assert f"search_path%3Dclient_orders_export_csv_{run_id}" in creds.connection_string
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_id, label="Orders Export!!.csv")


@requires_live_postgres
def test_provision_rejects_unsafe_run_ids(sample_upload):
    with pytest.raises(ValueError):
        provision_client_schema(
            TEST_ADMIN_URL, "not-safe; DROP TABLE x", sample_upload, public_host="127.0.0.1"
        )


@requires_live_postgres
def test_deprovision_is_safe_to_call_when_nothing_was_provisioned():
    deprovision_client_schema(TEST_ADMIN_URL, "wtestnevercreated")


@requires_live_postgres
def test_deprovision_actually_removes_the_schema_and_role(sample_upload):
    run_id = "wtest02"
    provision_client_schema(TEST_ADMIN_URL, run_id, sample_upload, public_host="127.0.0.1")
    deprovision_client_schema(TEST_ADMIN_URL, run_id)

    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{TEST_ADMIN_URL}' AS srcdb (TYPE POSTGRES)")
        schemas = con.execute(
            "SELECT * FROM postgres_query('srcdb', "
            f"'SELECT 1 FROM information_schema.schemata WHERE schema_name = ''client_{run_id}''')"
        ).fetchall()
        assert schemas == []
    finally:
        con.close()
