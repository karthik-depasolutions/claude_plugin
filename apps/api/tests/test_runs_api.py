from __future__ import annotations

import asyncio
import io
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

    confirm_url = f"/runs/{run_id}/confirm-industry"
    confirm_after_success = await client.post(confirm_url, json={"industry": "finance"})
    assert confirm_after_success.status_code == 409


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
