"""Run orchestration endpoints: create (from a server path or an upload),
poll status, stream progress over SSE, resolve a paused NEEDS_INPUT run
(industry confirmation / binding overrides), and download the packaged
plugin once validation succeeds."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import IO, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from forge_core.ingestion.registry import prepare_source_for_persistence
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord, StageEvent
from forge_core.packaging import zip_plugin
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
    RunDetail,
    RunSummary,
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
        run_id, source_for_run, output_dir, industry_override=body.industry, use_llm=body.use_llm
    )
    return _summary(ctx.record)


@router.post("/upload", response_model=RunSummary, status_code=201)
async def create_run_from_upload(
    files: list[UploadFile], industry: str | None = None, use_llm: bool = True
) -> RunSummary:
    """Accepts one or more files - multiple CSVs (or a mix of CSV/Excel/JSON/
    Parquet) become a multi-table source, same as pointing `forge run` at a
    directory. A single `.zip` is unpacked first so a customer can upload
    "all my tables" as one archive instead of selecting each file."""
    run_id = _new_run_id()
    source_dir = get_settings().runs_dir / run_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    ingest_path = _save_uploads(files, source_dir)

    output_dir = str(get_settings().runs_dir / run_id / "output")
    ctx = await pipeline_runner.start_run(
        run_id, str(ingest_path), output_dir, industry_override=industry, use_llm=use_llm
    )
    return _summary(ctx.record)


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
