"""End-to-end: an upload loaded through the client warehouse, run through the
*real* pipeline (ingest -> profile -> classify -> bind -> compile -> generate
-> validate -> package), produces a packaged plugin with no `data/` folder
and a `config/data_source.json` that only ever names the credential's env
var - never the literal connection string. Needs a real Postgres with
superuser rights - skips cleanly otherwise, same convention as
`test_warehouse.py`."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest
from forge_core.ingestion.postgres import CREDENTIAL_ENV_VAR, redact
from forge_core.ingestion.warehouse import deprovision_client_schema, provision_client_schema
from forge_core.models.common import RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import run_pipeline

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


@requires_live_postgres
def test_upload_through_the_warehouse_packages_with_no_data_folder_and_no_literal_credential(
    bookings_csv: Path, tmp_path: Path
):
    run_id = "e2ewarehouse01"
    creds = provision_client_schema(
        TEST_ADMIN_URL,
        run_id,
        bookings_csv,
        public_host="127.0.0.1",
        public_port=urlparse(TEST_ADMIN_URL).port or 5432,
    )
    try:
        record = RunRecord(run_id=run_id, source_path=creds.connection_string, output_dir=str(tmp_path))
        result = run_pipeline(record)
        assert result.status == RunStatus.SUCCEEDED, result.error

        plugin_dirs = list(tmp_path.iterdir())
        assert len(plugin_dirs) == 1
        plugin_dir = plugin_dirs[0]
        assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()

        # The whole point of the client warehouse: no bundled data/ folder,
        # and no copy of the client's data anywhere in the packaged plugin.
        assert not (plugin_dir / "data").exists()

        data_source_json = (plugin_dir / "config" / "data_source.json").read_text(encoding="utf-8")
        assert f"${{{CREDENTIAL_ENV_VAR}}}" in data_source_json
        assert creds.role_name not in data_source_json
        # No password, no host, no schema name - just the placeholder.
        password = creds.connection_string.split(":")[2].split("@")[0]
        assert password not in data_source_json
    finally:
        deprovision_client_schema(TEST_ADMIN_URL, run_id)
