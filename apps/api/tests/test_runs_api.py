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


async def _wait_for_terminal(client: AsyncClient, run_id: str, timeout: float = 90.0) -> dict:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await client.get(f"/runs/{run_id}")
        data = response.json()
        if data["status"] in ("succeeded", "failed", "needs_input"):
            return data
        await asyncio.sleep(0.3)
    raise TimeoutError(f"run {run_id} did not reach a terminal state in {timeout}s")


async def _wait_until_not_paused(client: AsyncClient, run_id: str, timeout: float = 30.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        data = (await client.get(f"/runs/{run_id}")).json()
        if data["status"] != "needs_input":
            return
        await asyncio.sleep(0.2)
    raise TimeoutError(f"run {run_id} did not leave needs_input in {timeout}s")


async def _wait_for_success(client: AsyncClient, run_id: str, timeout: float = 90.0) -> dict:
    """Waits for a terminal state; if P1-08's binding gate paused the run
    (most fixtures used here have at least one binding whose name-overlap
    confidence never clears MIN_CONFIDENCE_RESOLVED - see the resolver's own
    module docstring), confirms every proposed binding as-is - the
    resolver's own top pick - and waits again. Most of these tests aren't
    about the binding gate itself, just a real caller with nothing more
    informed to say than "yes, that's right"."""
    final = await _wait_for_terminal(client, run_id, timeout)
    if final["status"] == "needs_input" and final.get("binding_questions"):
        confirmations = {q["role"]: q["physical"] for q in final["binding_questions"]}
        response = await client.post(
            f"/runs/{run_id}/confirm-bindings", json={"confirmations": confirmations}
        )
        assert response.status_code == 200, response.text
        await _wait_until_not_paused(client, run_id, timeout)
        final = await _wait_for_terminal(client, run_id, timeout)
    return final


async def test_create_run_from_path_rejects_missing_source(client: AsyncClient):
    response = await client.post("/runs", json={"source_path": "does/not/exist.csv", "use_llm": False})
    assert response.status_code == 404


async def test_full_run_lifecycle_succeeds_and_is_downloadable(client: AsyncClient):
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    final = await _wait_for_success(client, run_id)
    assert final["status"] == "succeeded", final

    report = await client.get(f"/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.json()["overall"] in ("pass", "warn")

    download = await client.get(f"/runs/{run_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert len(download.content) > 0

    confirm_url = f"/runs/{run_id}/confirm-industry"
    confirm_after_success = await client.post(confirm_url, json={"industry": "finance"})
    assert confirm_after_success.status_code == 409


async def test_review_pause_and_resume_via_post_review(client: AsyncClient, monkeypatch):
    """The merged pause: with review questions present, a run pauses on
    needs_answers; POST /review (answers, no industry) resumes it; the
    answers are persisted and the run completes. build_data_review is
    stubbed to inject a question without an LLM."""
    from datetime import UTC, datetime

    import forge_core.orchestrator as orch
    from forge_core.models.quality import DataQuestion

    real_build_data_review = orch.build_data_review

    def _with_question(data_source, structural, con, *, provider=None, semantic=None):
        review = real_build_data_review(data_source, structural, con, provider=None, semantic=semantic)
        review.questions = [
            DataQuestion(
                id="dominant_value:bookings.status",
                question="What does the dominant status value mean?",
                context="80% of rows are 'confirmed'.",
            )
        ]
        review.generated_at = datetime.now(UTC).isoformat()
        return review

    monkeypatch.setattr(orch, "build_data_review", _with_question)

    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    paused = await _wait_for_terminal(client, run_id)
    assert paused["status"] == "needs_input", paused

    detail = await client.get(f"/runs/{run_id}")
    pause_event = next(e for e in detail.json()["events"] if e["data"].get("needs_answers") is not None)
    assert pause_event["data"]["needs_answers"] is True
    assert pause_event["data"]["needs_industry"] is False
    assert "data_context" not in pause_event  # data_review lives on RunDetail, not the pause event

    review_response = await client.post(
        f"/runs/{run_id}/review",
        json={"industry": None, "answers": {"dominant_value:bookings.status": "confirmed means booked"}},
    )
    assert review_response.status_code == 200, review_response.text

    # The resume task flips the status off needs_input asynchronously; wait
    # for the run to actually be re-scheduled before polling for terminal.
    await _wait_until_not_paused(client, run_id)

    final = await _wait_for_success(client, run_id)
    assert final["status"] == "succeeded", final

    detail = await client.get(f"/runs/{run_id}")
    assert detail.json()["data_answers"]["dominant_value:bookings.status"] == "confirmed means booked"

    # A resume must not re-report the pre-pause stages: the timeline stays
    # continuous (no re-ingest / re-classify "step 1" flash) - the resume
    # re-executes them but only appends post-pause events.
    events = detail.json()["events"]
    ingest_messages = [e["message"] for e in events if e["stage"] == "ingest"]
    assert sum(1 for m in ingest_messages if m.startswith("Ingesting")) == 1, events
    stages = [e["stage"] for e in events]
    first_bind = stages.index("bind")
    assert "ingest" not in stages[first_bind:], events


async def test_upload_accepts_multiple_csv_files_as_one_multi_table_run(client: AsyncClient):
    files = [
        ("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(RETAIL_ORDERS_DIR.glob("*.csv"))
    ]
    create_response = await client.post("/runs/upload", params={"use_llm": False}, files=files)
    assert create_response.status_code == 201
    run_id = create_response.json()["run_id"]

    final = await _wait_for_success(client, run_id)
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

    final = await _wait_for_success(client, run_id)
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
    await _wait_for_success(client, run_id)

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
    await _wait_for_success(client, run_id)

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
    final = await _wait_for_success(client, run_id)
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
    final = await _wait_for_success(client, run_id)
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
    await _wait_for_success(client, run_id)

    async with client.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.status_code == 200
        chunks = [chunk async for chunk in response.aiter_text()]
    body = "".join(chunks)
    assert "compile_kpis" in body
    assert '"final": true' in body


async def test_sse_after_slices_events_for_a_resumed_client(client: AsyncClient):
    """A client that resubscribes after a pause passes `?after=N` so the
    already-seen events (ingest/profile/classify/pause) are not replayed -
    this is what stops a resumed run looking like it started over."""
    create_response = await client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False}
    )
    run_id = create_response.json()["run_id"]
    await _wait_for_terminal(client, run_id)

    detail = await client.get(f"/runs/{run_id}")
    events = detail.json()["events"]
    assert events, "first pass must have produced events"

    async with client.stream("GET", f"/runs/{run_id}/events?after={len(events)}") as response:
        assert response.status_code == 200
        chunks = [chunk async for chunk in response.aiter_text()]
    body = "".join(chunks)
    # with after=N there is nothing new to send - the stream closes
    # immediately with only the final marker and no replayed events.
    assert '"final": true' in body
    assert '"stage":"ingest"' not in body
    assert "compile_kpis" not in body


async def test_admin_can_see_all_pending_generation_while_regular_user_only_sees_theirs(
    unauthenticated_client: AsyncClient,
):
    from forge_api.db import session_factory
    from forge_api.models_orm import UserORM
    from forge_api.security import hash_password

    # Create two users: user A (regular), user B (admin)
    async with session_factory()() as session:
        session.add(UserORM(email="alice@company.com", password_hash=hash_password("pw123456"), is_admin=False))
        session.add(UserORM(email="admin@company.com", password_hash=hash_password("pw123456"), is_admin=True))
        await session.commit()

    # Login as Alice (regular user)
    alice_login = await unauthenticated_client.post(
        "/auth/login", json={"email": "alice@company.com", "password": "pw123456"}
    )
    assert alice_login.status_code == 200
    assert not alice_login.json()["is_admin"]

    # Alice creates a run
    alice_run = await unauthenticated_client.post(
        "/runs", json={"source_path": str(BOOKINGS_CSV), "use_llm": False, "label": "Alice Plugin"}
    )
    assert alice_run.status_code == 201
    alice_run_id = alice_run.json()["run_id"]

    # Alice sees her run
    alice_runs = await unauthenticated_client.get("/runs")
    assert alice_runs.status_code == 200
    assert any(r["run_id"] == alice_run_id for r in alice_runs.json())

    # Logout Alice
    await unauthenticated_client.post("/auth/logout")

    # Login as Bob (another regular user)
    async with session_factory()() as session:
        session.add(UserORM(email="bob@company.com", password_hash=hash_password("pw123456"), is_admin=False))
        await session.commit()

    bob_login = await unauthenticated_client.post(
        "/auth/login", json={"email": "bob@company.com", "password": "pw123456"}
    )
    assert bob_login.status_code == 200

    # Bob's runs list should NOT include Alice's run!
    bob_runs = await unauthenticated_client.get("/runs")
    assert bob_runs.status_code == 200
    assert not any(r["run_id"] == alice_run_id for r in bob_runs.json())

    # Bob cannot access Alice's run directly
    bob_get_alice_run = await unauthenticated_client.get(f"/runs/{alice_run_id}")
    assert bob_get_alice_run.status_code == 404

    # Logout Bob
    await unauthenticated_client.post("/auth/logout")

    # Login as Admin
    admin_login = await unauthenticated_client.post(
        "/auth/login", json={"email": "admin@company.com", "password": "pw123456"}
    )
    assert admin_login.status_code == 200
    assert admin_login.json()["is_admin"]

    # Admin CAN see all runs across all users
    admin_runs = await unauthenticated_client.get("/runs?scope=all")
    assert admin_runs.status_code == 200
    assert any(r["run_id"] == alice_run_id for r in admin_runs.json())

    # Admin can access Alice's run directly
    admin_get_alice_run = await unauthenticated_client.get(f"/runs/{alice_run_id}")
    assert admin_get_alice_run.status_code == 200

