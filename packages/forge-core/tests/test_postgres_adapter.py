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
from forge_core.ingestion.postgres import CREDENTIAL_ENV_VAR, PostgresAdapter, redact
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
