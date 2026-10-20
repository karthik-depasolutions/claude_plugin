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
    CloneTooLargeError,
    deprovision_client_schema,
    provision_client_schema,
    provision_client_schema_from_postgres,
)

TEST_ADMIN_URL = os.environ.get(
    "FORGE_TEST_WAREHOUSE_ADMIN_URL", "postgresql://forge:forge@localhost:5432/forge"
)
# Derived from the admin URL itself, not hardcoded: true for docker-compose
# (host reachable at its own address) and a real managed Postgres like
# Supabase (publicly reachable, same address for admin and clients) alike.
# Only the old tunnel-only staging box needed a genuinely different
# "public" address than the admin one - that's the exception, not the rule.
_parsed_admin_url = urlparse(TEST_ADMIN_URL)
TEST_PUBLIC_HOST = _parsed_admin_url.hostname or "127.0.0.1"
TEST_PUBLIC_PORT = _parsed_admin_url.port or 5432
TEST_DATABASE = (_parsed_admin_url.path or "/forge").lstrip("/") or "forge"
# Only set when TEST_ADMIN_URL points at a pooler (e.g. Supabase's) that
# needs "<role>.<project_ref>" usernames - see provision_client_schema's
# docstring. None for a plain docker-compose/direct Postgres.
TEST_PUBLIC_USERNAME_SUFFIX = os.environ.get("FORGE_TEST_WAREHOUSE_PUBLIC_USERNAME_SUFFIX")


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
            public_host=TEST_PUBLIC_HOST,
            public_port=TEST_PUBLIC_PORT,
            database=TEST_DATABASE,
            public_username_suffix=TEST_PUBLIC_USERNAME_SUFFIX,
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
def test_a_runs_role_cannot_read_another_runs_schema(sample_upload):
    """Guiding principle #6: one client's data never reaches another's plugin.
    Provision two schemas, then use run A's own connection string to try to
    read run B's table by its fully-qualified name (which bypasses the pinned
    search_path). The `_ro` role has no USAGE on schema B, so it must fail."""
    run_a, run_b = "wisoa01", "wisob01"
    try:
        creds_a = provision_client_schema(
            TEST_ADMIN_URL, run_a, sample_upload,
            public_host=TEST_PUBLIC_HOST, public_port=TEST_PUBLIC_PORT, database=TEST_DATABASE,
            public_username_suffix=TEST_PUBLIC_USERNAME_SUFFIX,
        )
        provision_client_schema(
            TEST_ADMIN_URL, run_b, sample_upload,
            public_host=TEST_PUBLIC_HOST, public_port=TEST_PUBLIC_PORT, database=TEST_DATABASE,
            public_username_suffix=TEST_PUBLIC_USERNAME_SUFFIX,
        )

        a_url = creds_a.connection_string.replace("sslmode=require", "sslmode=prefer")
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL postgres; LOAD postgres;")
            con.execute(f"ATTACH '{a_url}' AS a (TYPE POSTGRES)")
            # sanity: role A CAN read its own schema
            own = con.execute("SELECT count(*) FROM postgres_query('a', 'SELECT * FROM orders')").fetchone()
            assert own[0] == 2
            # ...and CANNOT read schema B, even fully qualified
            with pytest.raises((duckdb.Error, RuntimeError)):
                con.execute(
                    "SELECT * FROM postgres_query('a', "
                    f"'SELECT count(*) FROM \"client_{run_b}\".\"orders\"')"
                ).fetchall()
        finally:
            con.close()
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_a)
        deprovision_client_schema(TEST_ADMIN_URL, run_b)


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
def test_provision_with_a_username_suffix_builds_a_pooler_style_username(sample_upload):
    run_id = "wtest04"
    try:
        creds = provision_client_schema(
            TEST_ADMIN_URL,
            run_id,
            sample_upload,
            public_host=TEST_PUBLIC_HOST,
            public_port=TEST_PUBLIC_PORT,
            database=TEST_DATABASE,
            public_username_suffix="my-project-ref",
        )
        # e.g. postgresql://client_wtest04_ro.my-project-ref:...@host:port/db -
        # the pooler-required "<role>.<project_ref>" username shape.
        assert f"client_{run_id}_ro.my-project-ref:" in creds.connection_string
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_id)


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


# --------------------------------------------------------------------------- #
# Cloning a source Postgres schema into the warehouse.
# --------------------------------------------------------------------------- #
def _make_source_schema(schema: str, *, rows_b: int = 3) -> str:
    """Create `<schema>.a` (2 rows) and `<schema>.b` (`rows_b` rows) on the
    test server, and return a connection string whose search_path selects it -
    the shape a customer's `postgresql://…?options=-csearch_path%3D<schema>`
    connection has."""
    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{TEST_ADMIN_URL}' AS srcdb (TYPE POSTGRES)")
        con.execute(f'CALL postgres_execute(\'srcdb\', $d$DROP SCHEMA IF EXISTS "{schema}" CASCADE$d$)')
        con.execute(f'CALL postgres_execute(\'srcdb\', $d$CREATE SCHEMA "{schema}"$d$)')
        con.execute(f'CREATE TABLE srcdb."{schema}"."a" AS SELECT * FROM (VALUES (1), (2)) t(id)')
        vals = ", ".join(f"({i})" for i in range(rows_b))
        con.execute(f'CREATE TABLE srcdb."{schema}"."b" AS SELECT * FROM (VALUES {vals}) t(id)')
    finally:
        con.close()
    sep = "&" if "?" in TEST_ADMIN_URL else "?"
    return f"{TEST_ADMIN_URL}{sep}options=-csearch_path%3D{schema}"


def _drop_source_schema(schema: str) -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{TEST_ADMIN_URL}' AS srcdb (TYPE POSTGRES)")
        con.execute(f'CALL postgres_execute(\'srcdb\', $d$DROP SCHEMA IF EXISTS "{schema}" CASCADE$d$)')
    finally:
        con.close()


@requires_live_postgres
def test_clone_from_postgres_copies_the_schema_with_matching_row_counts():
    run_id = "wtestc1"
    src_schema = "wtest_clonesrc1"
    source_url = _make_source_schema(src_schema, rows_b=4)
    try:
        creds = provision_client_schema_from_postgres(
            TEST_ADMIN_URL,
            run_id,
            source_url,
            public_host=TEST_PUBLIC_HOST,
            public_port=TEST_PUBLIC_PORT,
            database=TEST_DATABASE,
            public_username_suffix=TEST_PUBLIC_USERNAME_SUFFIX,
        )
        assert creds.schema_name == f"client_{run_id}"
        assert f"search_path%3Dclient_{run_id}" in creds.connection_string

        readback_url = creds.connection_string.replace("sslmode=require", "sslmode=prefer")
        ds = PostgresAdapter().ingest(readback_url)
        by_name = {t.name: t.row_count for t in ds.tables}
        assert by_name == {"a": 2, "b": 4}  # the copy, read via the scoped _ro role
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_id)
        _drop_source_schema(src_schema)


@requires_live_postgres
def test_clone_hard_fails_over_the_row_cap_and_leaves_nothing(monkeypatch):
    monkeypatch.setattr("forge_core.ingestion.warehouse.MAX_CLONE_TOTAL_ROWS", 1)
    run_id = "wtestc2"
    src_schema = "wtest_clonesrc2"
    source_url = _make_source_schema(src_schema)
    try:
        with pytest.raises(CloneTooLargeError):
            provision_client_schema_from_postgres(
                TEST_ADMIN_URL, run_id, source_url, public_host=TEST_PUBLIC_HOST,
                public_port=TEST_PUBLIC_PORT, database=TEST_DATABASE,
                public_username_suffix=TEST_PUBLIC_USERNAME_SUFFIX,
            )
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL postgres; LOAD postgres;")
            con.execute(f"ATTACH '{TEST_ADMIN_URL}' AS srcdb (TYPE POSTGRES)")
            left = con.execute(
                "SELECT * FROM postgres_query('srcdb', "
                f"'SELECT 1 FROM information_schema.schemata WHERE schema_name = ''client_{run_id}''')"
            ).fetchall()
            assert left == []  # caps checked before anything is created
        finally:
            con.close()
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_id)
        _drop_source_schema(src_schema)


def test_wait_until_role_is_connectable_raises_when_the_role_never_comes_up(monkeypatch):
    """No live Postgres: a role that never becomes reachable must raise (so the
    caller rolls the partial schema back) instead of silently handing out a
    connection string the shipped plugin can't use either."""
    from forge_core.ingestion import warehouse

    class _FakeCon:
        def execute(self, sql: str):
            if sql.startswith("ATTACH"):
                raise RuntimeError("password authentication failed for user")
            return self

        def close(self) -> None:
            pass

    monkeypatch.setattr(warehouse.duckdb, "connect", lambda *a, **k: _FakeCon())
    monkeypatch.setattr(warehouse.time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="not connectable"):
        warehouse._wait_until_role_is_connectable("host", 5432, "db", "role.project", "pw")
