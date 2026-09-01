from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

from httpx import AsyncClient

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOKINGS_CSV = REPO_ROOT / "fixtures" / "datasets" / "bookings.csv"
RETAIL_ORDERS_DIR = REPO_ROOT / "fixtures" / "datasets" / "retail_orders"


async def _wait_for_terminal(
    client: AsyncClient, run_id: str, timeout: float = 90.0, *, auto_answer: bool = True
) -> dict:
    """Waits for succeeded/failed. `auto_answer` transparently skips the
    pre-synthesis data-clarification pause (a POST of empty answers) so tests
    that only care about the end state don't have to; it still returns on a
    classify-stage `needs_input` (industry confirmation)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        data = (await client.get(f"/runs/{run_id}")).json()
        if data["status"] == "needs_input" and auto_answer and data.get("current_stage") == "profile":
            await client.post(f"/runs/{run_id}/answers", json={"answers": {}})
            await asyncio.sleep(0.3)
            continue
        if data["status"] in ("succeeded", "failed", "needs_input"):
            return data
        await asyncio.sleep(0.3)
    raise TimeoutError(f"run {run_id} did not reach a terminal state in {timeout}s")


async def test_create_run_from_path_rejects_missing_source(client: AsyncClient):
    response = await client.post("/runs", json={"source_path": "does/not/exist.csv", "use_llm": False})
    assert response.status_code == 404


async def test_full_run_lifecycle_succeeds_and_is_downloadable(client: AsyncClient):
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    final = await _wait_for_terminal(client, run_id)
    assert final["status"] == "succeeded", final

    report = await client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.json()["overall"] in ("pass", "warn")

    download = await client.get(f"/runs/{run_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert len(download.content) > 0

    # LLM token-usage columns are populated on the run row. The hermetic suite
    # uses a fake provider (no real tokens), so the counts are 0 - what this
    # guards is that the columns exist and the snapshot is written.
    from forge_api.db import session_factory
    from forge_api.models_orm import RunORM
    from sqlalchemy import select

    async with session_factory()() as session:
        row = (await session.execute(select(RunORM).where(RunORM.run_id == run_id))).scalar_one()
    assert row.llm_input_tokens == 0
    assert row.llm_output_tokens == 0
    assert row.llm_token_usage_json == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0, "by_model": {}, "by_role": {}
    }

    confirm_url = f"/runs/{run_id}/confirm-industry"
    confirm_after_success = await client.post(confirm_url, json={"industry": "finance"})
    assert confirm_after_success.status_code == 409
    # /answers is likewise a NEEDS_INPUT-only resume, 409 once the run is done
    answers_after_success = await client.post(f"/runs/{run_id}/answers", json={"answers": {}})
    assert answers_after_success.status_code == 409


async def test_data_clarification_pause_can_be_answered(client: AsyncClient):
    create = await client.post("/runs", json={"source_path": str(BOOKINGS_CSV)})
    run_id = create.json()["run_id"]

    paused = await _wait_for_terminal(client, run_id, auto_answer=False)
    if paused["status"] == "needs_input" and paused.get("current_stage") == "profile":
        detail = (await client.get(f"/runs/{run_id}")).json()
        questions = detail["data_review"]["questions"]
        assert questions, "a profile-stage pause must carry questions"
        resumed = await client.post(
            f"/runs/{run_id}/answers", json={"answers": {questions[0]["id"]: "a clarification"}}
        )
        assert resumed.status_code == 200
        final = await _wait_for_terminal(client, run_id)
        assert final["status"] in ("succeeded", "needs_input")  # may still pause for industry
    else:
        assert paused["status"] in ("succeeded", "needs_input")


async def test_upload_accepts_multiple_csv_files_as_one_multi_table_run(client: AsyncClient):
    files = [
        ("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(RETAIL_ORDERS_DIR.glob("*.csv"))
    ]
    create_response = await client.post("/runs/upload", params={"use_llm": False}, files=files)
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    final = await _wait_for_terminal(client, run_id)
    assert final["status"] == "succeeded", final


async def test_upload_accepts_a_zip_of_csv_files(client: AsyncClient):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for csv_path in RETAIL_ORDERS_DIR.glob("*.csv"):
            zf.writestr(csv_path.name, csv_path.read_bytes())
    buffer.seek(0)

    files = [("files", ("retail_orders.zip", buffer.read(), "application/zip"))]
    create_response = await client.post("/runs/upload", params={"use_llm": False}, files=files)
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    final = await _wait_for_terminal(client, run_id)
    assert final["status"] == "succeeded", final


async def test_upload_rejects_a_zip_with_a_path_traversal_entry(client: AsyncClient):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../../evil.csv", "a,b\n1,2\n")
    buffer.seek(0)

    files = [("files", ("evil.zip", buffer.read(), "application/zip"))]
    response = await client.post("/runs/upload", params={"use_llm": False}, files=files)
    assert response.status_code == 400


async def test_upload_routes_through_the_client_warehouse_when_configured(
    client: AsyncClient, monkeypatch
):
    """Doesn't need a real Postgres - `provision_client_schema` itself is
    mocked, so this only exercises the router's wiring: it's invoked with the
    upload directory, the returned connection string is never persisted (the
    `${FORGE_SOURCE_DB_URL}` placeholder is), and it's retrievable exactly
    once via the dedicated credentials endpoint."""
    from forge_api.routers import runs as runs_router
    from forge_core.ingestion.warehouse import WarehouseCredentials

    monkeypatch.setenv("FORGE_CLIENT_WAREHOUSE_URL", "postgresql://admin:pw@127.0.0.1:5432/forge_warehouse")
    monkeypatch.setenv("FORGE_CLIENT_WAREHOUSE_PUBLIC_HOST", "example.invalid")

    captured: dict = {}
    fake_connection_string = (
        "postgresql://client_fake_ro:s3cr3t@example.invalid:5432/forge_warehouse"
        "?options=-csearch_path%3Dclient_fake&sslmode=require"
    )

    def fake_provision(
        admin_url,
        run_id,
        upload_path,
        *,
        public_host,
        public_port,
        database,
        label=None,
        public_username_suffix=None,
    ):
        captured.update(
            admin_url=admin_url,
            run_id=run_id,
            upload_path=upload_path,
            public_host=public_host,
            public_port=public_port,
            database=database,
            label=label,
        )
        assert upload_path.exists(), "upload files must be saved to disk before provisioning runs"
        return WarehouseCredentials(
            connection_string=fake_connection_string, schema_name="client_fake", role_name="client_fake_ro"
        )

    monkeypatch.setattr(runs_router, "provision_client_schema", fake_provision)
    monkeypatch.setattr(runs_router, "deprovision_client_schema", lambda *a, **k: None)

    files = [("files", (BOOKINGS_CSV.name, BOOKINGS_CSV.read_bytes(), "text/csv"))]
    create_response = await client.post("/runs/upload", params={"use_llm": False}, files=files)
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    assert captured["run_id"] == run_id
    assert captured["public_host"] == "example.invalid"
    assert captured["label"] == BOOKINGS_CSV.stem, "schema label should hint at the uploaded file's name"
    assert not captured["upload_path"].exists(), "local upload copy must be removed once loaded"

    detail = await client.get(f"/runs/{run_id}")
    assert detail.json()["source_path"] == "${FORGE_SOURCE_DB_URL}"
    assert "s3cr3t" not in detail.text

    creds_response = await client.get(f"/runs/{run_id}/warehouse-credentials")
    assert creds_response.status_code == 200
    assert creds_response.json()["connection_string"] == fake_connection_string


async def test_warehouse_credentials_404_for_a_run_that_did_not_use_the_warehouse(client: AsyncClient):
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]

    response = await client.get(f"/runs/{run_id}/warehouse-credentials")
    assert response.status_code == 404


async def test_publish_to_github_requires_a_successful_run(client: AsyncClient, monkeypatch):
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]

    # Still running (or at least not yet succeeded) - must be rejected before
    # ever touching GITHUB_TOKEN or the network.
    response = await client.post(f"/runs/{run_id}/publish/github", json={})
    assert response.status_code in (409,)


async def test_publish_to_github_requires_a_configured_token(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]
    await _wait_for_terminal(client, run_id)

    response = await client.post(f"/runs/{run_id}/publish/github", json={})
    assert response.status_code == 400
    assert "GITHUB_TOKEN" in response.json()["detail"]


async def test_publish_to_github_requires_a_public_base_url(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.delenv("FORGE_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("FORGE_PUBLIC_BASE_URL", "")
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]
    await _wait_for_terminal(client, run_id)

    response = await client.post(f"/runs/{run_id}/publish/github", json={})
    assert response.status_code == 400
    assert "FORGE_PUBLIC_BASE_URL" in response.json()["detail"]


async def test_publish_to_github_creates_a_repo_and_returns_install_commands(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("FORGE_PUBLIC_BASE_URL", "https://forge.example")

    from forge_api.routers import runs as runs_router
    from forge_core.publishing.standalone_repo import PublishedRepo

    captured: dict = {}

    def fake_publish(plugin_dir, *, token, repo_name, owner, private):
        captured.update(
            plugin_dir=plugin_dir, token=token, repo_name=repo_name, owner=owner, private=private
        )
        return PublishedRepo(
            repo_full_name="acme/bookings-mis-plugin",
            html_url="https://github.com/acme/bookings-mis-plugin",
            clone_url="https://github.com/acme/bookings-mis-plugin.git",
            plugin_name="healthcare-diagnostics-mis-plugin",
            marketplace_add_command="/plugin marketplace add acme/bookings-mis-plugin",
            install_command="/plugin install healthcare-diagnostics-mis-plugin@bookings-mis-plugin",
        )

    monkeypatch.setattr(runs_router, "publish_plugin_as_new_repo", fake_publish)

    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]
    final = await _wait_for_terminal(client, run_id)
    assert final["status"] == "succeeded", final

    response = await client.post(
        f"/runs/{run_id}/publish/github", json={"repo_name": "bookings-mis-plugin", "private": False}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repo_full_name"] == "acme/bookings-mis-plugin"
    assert body["marketplace_add_command"] == "/plugin marketplace add acme/bookings-mis-plugin"
    assert captured["repo_name"] == "bookings-mis-plugin"
    assert captured["private"] is False
    assert captured["token"] == "fake-token"
    mcp = json.loads((captured["plugin_dir"] / ".mcp.json").read_text(encoding="utf-8"))
    url = mcp["mcpServers"]["mis-mcp-runtime"]["url"]
    assert url.startswith("https://forge.example/mcp/")
    assert mcp["mcpServers"]["mis-mcp-runtime"]["type"] == "http"


async def test_hosted_mcp_rejects_a_bad_token_and_serves_tools_with_a_good_one(
    client: AsyncClient, isolated_env
):
    from forge_core.packaging import ensure_mcp_token

    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]
    final = await _wait_for_terminal(client, run_id)
    assert final["status"] == "succeeded", final

    bad = await client.post(f"/mcp/{run_id}/not-the-token")
    assert bad.status_code == 401

    token = ensure_mcp_token(isolated_env / run_id)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    ok = await client.post(
        f"/mcp/{run_id}/{token}",
        json=init,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    assert ok.status_code != 401
    assert ok.status_code < 500


async def test_sse_stream_replays_events_for_a_finished_run(client: AsyncClient):
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]
    await _wait_for_terminal(client, run_id)

    async with client.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.status_code == 200
        chunks = [chunk async for chunk in response.aiter_text()]
    body = "".join(chunks)
    assert "compile_kpis" in body
    assert '"final": true' in body
