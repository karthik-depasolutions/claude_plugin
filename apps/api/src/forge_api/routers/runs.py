"""Run orchestration endpoints: create (from a server path or an upload),
poll status, stream progress over SSE, resolve a paused NEEDS_INPUT run
(industry confirmation / binding overrides), and download the packaged
plugin once validation succeeds."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import IO, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from forge_core.ingestion.registry import prepare_source_for_persistence
from forge_core.ingestion.warehouse import deprovision_client_schema, provision_client_schema
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord, StageEvent
from forge_core.packaging import bind_http_mcp, ensure_mcp_token, hosted_mcp_url, zip_plugin
from forge_core.publishing import publish_plugin_as_new_repo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge_api import pipeline_runner, registry
from forge_api.config import get_settings
from forge_api.db import get_session
from forge_api.models_orm import RunORM
from forge_api.schemas import (
    BindingOverridesRequest,
    ConfirmIndustryRequest,
    CreateRunFromPathRequest,
    PublishGithubRequest,
    PublishGithubResponse,
    RunDetail,
    RunSummary,
    WarehouseCredentialsResponse,
)

router = APIRouter(prefix="/runs", tags=["runs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


@router.post("", response_model=RunSummary, status_code=201)
async def create_run_from_path(body: CreateRunFromPathRequest) -> RunSummary:
    # `body.source_path` may be a filesystem path *or* a live-database
    # connection string (postgresql://...) - prepare_source_for_persistence
    # tells them apart and, for the latter, stashes the credential in this
    # process's environment and hands back a `${VAR}` placeholder so the
    # raw connection string is never stored on the RunRecord, the jobs DB,
    # or echoed back in a response.
    source_for_run = prepare_source_for_persistence(body.source_path)
    is_live_database = source_for_run.startswith("${")
    if not is_live_database and not Path(source_for_run).exists():
        raise HTTPException(404, f"source_path does not exist on the API host: {source_for_run}")

    run_id = _new_run_id()
    output_dir = str(get_settings().runs_dir / run_id)
    ctx = await pipeline_runner.start_run(
        run_id,
        source_for_run,
        output_dir,
        industry_override=body.industry,
        use_llm=body.use_llm,
        use_agent=body.use_agent,
    )
    return _summary(ctx.record)


@router.post("/upload", response_model=RunSummary, status_code=201)
async def create_run_from_upload(
    files: list[UploadFile], industry: str | None = None, use_llm: bool = True, use_agent: bool = False
) -> RunSummary:
    """Accepts one or more files - multiple CSVs (or a mix of CSV/Excel/JSON/
    Parquet) become a multi-table source, same as pointing `forge run` at a
    directory. A single `.zip` is unpacked first so a customer can upload
    "all my tables" as one archive instead of selecting each file.

    If the client data warehouse is configured (`FORGE_CLIENT_WAREHOUSE_URL`),
    the upload is loaded into a dedicated Postgres schema instead of being
    kept as local files - see forge_core.ingestion.warehouse. Otherwise this
    behaves exactly as before."""
    run_id = _new_run_id()
    source_dir = get_settings().runs_dir / run_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    ingest_path = _save_uploads(files, source_dir)

    settings = get_settings()
    warehouse_connection_string: str | None = None
    if settings.client_warehouse_enabled:
        warehouse_connection_string = await asyncio.to_thread(
            _provision_and_get_source, run_id, ingest_path, source_dir
        )
        # Never let the raw credential reach RunRecord/the jobs DB/a log line -
        # same placeholder scheme create_run_from_path already uses for a
        # customer-supplied postgresql:// source.
        source_for_run = prepare_source_for_persistence(warehouse_connection_string)
    else:
        source_for_run = str(ingest_path)

    output_dir = str(get_settings().runs_dir / run_id / "output")
    ctx = await pipeline_runner.start_run(
        run_id, source_for_run, output_dir, industry_override=industry, use_llm=use_llm, use_agent=use_agent
    )
    if warehouse_connection_string is not None:
        ctx.warehouse_connection_string = warehouse_connection_string
    return _summary(ctx.record)


def _label_for_upload(ingest_path: Path) -> str | None:
    """A short, human-readable hint for the schema name - purely so someone
    poking around in pgAdmin can tell `client_sales_data_6c34dcc7569a` apart
    from `client_customer_list_a2579a24b4b1` without cross-referencing the
    API. Best-effort: falls back to no label rather than failing the run."""
    try:
        if ingest_path.is_file():
            return ingest_path.stem
        files = sorted(p for p in ingest_path.iterdir() if p.is_file())
        return files[0].stem if files else None
    except OSError:
        return None


def _provision_and_get_source(run_id: str, ingest_path: Path, source_dir: Path) -> str:
    """Loads the upload into the warehouse and returns the raw connection
    string - the caller immediately passes this through
    `prepare_source_for_persistence` before it touches `RunRecord`, exactly
    like a customer-supplied live-database source already works today.

    Runs on a thread (it makes a real network connection) and best-effort
    rolls back a partial schema if anything fails partway through."""
    settings = get_settings()
    assert settings.client_warehouse_url and settings.client_warehouse_public_host  # checked by caller
    label = _label_for_upload(ingest_path)
    try:
        creds = provision_client_schema(
            settings.client_warehouse_url,
            run_id,
            ingest_path,
            public_host=settings.client_warehouse_public_host,
            public_port=settings.client_warehouse_public_port,
            database=settings.client_warehouse_database,
            label=label,
            public_username_suffix=settings.client_warehouse_public_username_suffix,
        )
    except Exception:
        deprovision_client_schema(settings.client_warehouse_url, run_id, label=label)
        raise
    finally:
        # The uploaded files' only job was to get loaded into Postgres above;
        # don't leave a second, unredacted copy sitting on our own disk.
        shutil.rmtree(source_dir, ignore_errors=True)
    return creds.connection_string


def _save_uploads(files: list[UploadFile], source_dir: Path) -> Path:
    """Saves the upload(s) under `source_dir` and returns the path to hand
    to `ingest()`. A single file is returned as-is (one-table source); a
    zip is unpacked and, if it just wrapped one top-level folder (the
    common "zip a folder" case on Windows/macOS), that folder is used
    directly so nested tables are still found."""
    if len(files) == 1 and not (files[0].filename or "").lower().endswith(".zip"):
        dest = source_dir / (files[0].filename or "upload.dat")
        with dest.open("wb") as out:
            shutil.copyfileobj(files[0].file, out)
        return dest

    if len(files) == 1:
        _extract_zip_safely(files[0].file, source_dir)
        entries = list(source_dir.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return source_dir

    for file in files:
        dest = source_dir / (file.filename or "upload.dat")
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    return source_dir


def _extract_zip_safely(fileobj: IO[bytes], source_dir: Path) -> None:
    """Extracts only regular files whose resolved path stays inside
    `source_dir`, rejecting absolute paths, `..` traversal, and symlinks
    (the classic "zip slip" vulnerability) before anything is written."""
    resolved_root = source_dir.resolve()
    with zipfile.ZipFile(fileobj) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (source_dir / info.filename).resolve()
            if target != resolved_root and resolved_root not in target.parents:
                raise HTTPException(400, f"Refusing to extract unsafe zip entry: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


@router.get("", response_model=list[RunSummary])
async def list_runs(session: SessionDep) -> list[RunSummary]:
    result = await session.execute(select(RunORM).order_by(RunORM.created_at.desc()))
    rows = result.scalars().all()
    return [
        RunSummary(run_id=r.run_id, status=r.status, current_stage=r.current_stage, error=r.error)
        for r in rows
    ]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: SessionDep) -> RunDetail:
    return RunDetail.model_validate((await _load_record(run_id, session)).model_dump())


@router.get("/{run_id}/events")
async def stream_run_events(run_id: str, session: SessionDep) -> StreamingResponse:
    await _load_record(run_id, session)  # 404s early if the run truly doesn't exist

    async def gen() -> Any:
        sent = 0
        while True:
            ctx = registry.get(run_id)
            if ctx is None:
                # Not live in this process (e.g. after a restart) - emit what's on
                # disk once and close; there is nothing further to stream.
                record = await _load_record(run_id, session)
                for event in record.events[sent:]:
                    yield _sse(event.model_dump(mode="json"))
                return

            events = ctx.record.events
            for event in events[sent:]:
                yield _sse(event.model_dump(mode="json"))
            sent = len(events)

            if not ctx.running and ctx.record.status != RunStatus.RUNNING:
                yield _sse({"final": True, "status": ctx.record.status.value})
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{run_id}/confirm-industry", response_model=RunSummary)
async def confirm_industry(run_id: str, body: ConfirmIndustryRequest, session: SessionDep) -> RunSummary:
    ctx = await _require_live_context(run_id, session)
    _require_status(ctx.record, RunStatus.NEEDS_INPUT)
    ctx.record.industry_override = body.industry
    await pipeline_runner.resume_run(ctx, use_llm=True)
    return _summary(ctx.record)


@router.post("/{run_id}/bindings", response_model=RunSummary)
async def set_binding_overrides(
    run_id: str, body: BindingOverridesRequest, session: SessionDep
) -> RunSummary:
    ctx = await _require_live_context(run_id, session)
    if ctx.record.status not in (RunStatus.NEEDS_INPUT, RunStatus.SUCCEEDED, RunStatus.FAILED):
        raise HTTPException(409, f"Cannot set binding overrides while run is {ctx.record.status.value}.")
    ctx.binding_overrides.update(body.overrides)
    await pipeline_runner.resume_run(ctx, use_llm=True)
    return _summary(ctx.record)


@router.get("/{run_id}/report")
async def get_report(run_id: str, session: SessionDep) -> dict:
    record = await _load_record(run_id, session)
    event = _last_event(record, RunStage.VALIDATE)
    if event is None or "report" not in event.data:
        raise HTTPException(404, "No validation report yet for this run.")
    return event.data["report"]


@router.get("/{run_id}/download")
async def download_plugin(run_id: str, session: SessionDep) -> FileResponse:
    record = await _load_record(run_id, session)
    event = _last_event(record, RunStage.PACKAGE)
    if event is None or "plugin_dir" not in event.data:
        raise HTTPException(404, "This run hasn't produced a packaged plugin yet.")

    plugin_dir = Path(event.data["plugin_dir"])
    if not plugin_dir.is_dir():
        raise HTTPException(410, "Packaged plugin output is no longer on disk.")

    zip_path = plugin_dir.with_suffix(".zip")
    if not zip_path.exists() or zip_path.stat().st_mtime < plugin_dir.stat().st_mtime:
        zip_plugin(plugin_dir, zip_path)
    return FileResponse(zip_path, filename=zip_path.name, media_type="application/zip")


@router.get("/{run_id}/warehouse-credentials", response_model=WarehouseCredentialsResponse)
async def get_warehouse_credentials(run_id: str, session: SessionDep) -> WarehouseCredentialsResponse:
    """Returns the connection string once, from this process's memory only -
    see `RunContext.warehouse_connection_string`. 404s for any run that
    either wasn't loaded through the warehouse, or whose API process has
    since restarted (there is deliberately no persisted copy to fall back
    to)."""
    await _load_record(run_id, session)  # 404s early if the run truly doesn't exist
    ctx = registry.get(run_id)
    if ctx is None or ctx.warehouse_connection_string is None:
        raise HTTPException(
            404,
            "No warehouse credentials available for this run (either it wasn't loaded via the "
            "client warehouse, or this API process restarted since - credentials are never stored).",
        )
    return WarehouseCredentialsResponse(connection_string=ctx.warehouse_connection_string)


@router.post("/{run_id}/publish/github", response_model=PublishGithubResponse)
async def publish_run_to_github(
    run_id: str, body: PublishGithubRequest, session: SessionDep
) -> PublishGithubResponse:
    """Creates a brand-new GitHub repo for this run's packaged plugin and
    pushes it - the repo is immediately installable in Claude Code/Desktop
    with the two commands this returns. Only available once validation has
    passed (RunStatus.SUCCEEDED); see forge_core.publishing.standalone_repo."""
    record = await _load_record(run_id, session)
    if record.status != RunStatus.SUCCEEDED:
        raise HTTPException(
            409, f"Run is {record.status.value}; publish is only available after a successful run."
        )
    event = _last_event(record, RunStage.PACKAGE)
    if event is None or "plugin_dir" not in event.data:
        raise HTTPException(404, "This run hasn't produced a packaged plugin yet.")

    plugin_dir = Path(event.data["plugin_dir"])
    if not plugin_dir.is_dir():
        raise HTTPException(410, "Packaged plugin output is no longer on disk.")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HTTPException(400, "GITHUB_TOKEN is not configured on the API server; can't publish to GitHub.")

    settings = get_settings()
    if not settings.public_base_url:
        raise HTTPException(
            400,
            "FORGE_PUBLIC_BASE_URL is not set. Claude Desktop will not auto-connect a local "
            "python run_server.py from a GitHub plugin. Set FORGE_PUBLIC_BASE_URL to a public "
            "HTTPS origin that reaches this API (a tunnel URL is fine for demos) and retry.",
        )
    mcp_token = ensure_mcp_token(Path(record.output_dir))
    bind_http_mcp(plugin_dir, hosted_mcp_url(settings.public_base_url, run_id, mcp_token))

    try:
        result = await asyncio.to_thread(
            publish_plugin_as_new_repo,
            plugin_dir,
            token=token,
            repo_name=body.repo_name,
            owner=body.owner or os.environ.get("GITHUB_ORG"),
            private=body.private,
        )
    except Exception as exc:  # GithubException, ValueError from an invalid plugin, network errors, ...
        raise HTTPException(502, f"Publishing to GitHub failed: {exc}") from exc

    return PublishGithubResponse(
        repo_full_name=result.repo_full_name,
        html_url=result.html_url,
        plugin_name=result.plugin_name,
        marketplace_add_command=result.marketplace_add_command,
        install_command=result.install_command,
    )


def _summary(record: RunRecord) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        status=record.status.value,
        current_stage=record.current_stage.value if record.current_stage else None,
        error=record.error,
    )


def _last_event(record: RunRecord, stage: RunStage) -> StageEvent | None:
    for event in reversed(record.events):
        if event.stage == stage:
            return event
    return None


def _require_status(record: RunRecord, expected: RunStatus) -> None:
    if record.status != expected:
        raise HTTPException(409, f"Run is {record.status.value}, expected {expected.value}.")


async def _require_live_context(run_id: str, session: SessionDep) -> registry.RunContext:
    await _load_record(run_id, session)  # 404s if unknown
    ctx = registry.get(run_id)
    if ctx is None:
        raise HTTPException(409, "Run isn't live in this API process (e.g. after a restart); can't resume.")
    return ctx


async def _load_record(run_id: str, session: SessionDep) -> RunRecord:
    ctx = registry.get(run_id)
    if ctx is not None:
        return ctx.record

    row = await session.get(RunORM, run_id)
    if row is None:
        raise HTTPException(404, f"No run with id {run_id!r}.")
    return RunRecord.model_validate(row.record_json)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
