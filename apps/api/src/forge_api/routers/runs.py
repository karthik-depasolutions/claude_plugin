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

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
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
from forge_api.models_orm import RunORM, UserORM
from forge_api.routers.auth import get_current_user
from forge_api.schemas import (
    BindingConfirmationRequest,
    BindingOverridesRequest,
    ConfirmIndustryRequest,
    CreateRunFromPathRequest,
    PublishGithubRequest,
    PublishGithubResponse,
    ReviewRequest,
    RunDetail,
    RunSummary,
    WarehouseCredentialsResponse,
)

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


@router.post("", response_model=RunSummary, status_code=201)
async def create_run_from_path(
    body: CreateRunFromPathRequest, user: Annotated[UserORM, Depends(get_current_user)]
) -> RunSummary:
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
        label=body.label,
        tenant_id=user.email,
    )
    return _summary(ctx.record)


@router.post("/upload", response_model=RunSummary, status_code=201)
async def create_run_from_upload(
    files: list[UploadFile],
    user: Annotated[UserORM, Depends(get_current_user)],
    industry: str | None = None,
    use_llm: bool = True,
    use_agent: bool = True,
    label: str | None = None,
) -> RunSummary:
    """Accepts one or more files - multiple CSVs (or a mix of CSV/Excel/JSON/
    Parquet) become a multi-table source, same as pointing `forge run` at a
    directory. A single `.zip` is unpacked first so a customer can upload
    "all my tables" as one archive instead of selecting each file.

    If the client data warehouse is configured (`FORGE_CLIENT_WAREHOUSE_URL`),
    the upload is loaded into a dedicated Postgres schema instead of being
    kept as local files - see forge_core.ingestion.warehouse. Otherwise this
    behaves exactly as before.

    `label`, if given, names both that Postgres schema and the packaged
    plugin (see `_provision_and_get_source` / `plugin_name_for`) instead of
    the schema falling back to the first uploaded file's own name."""
    run_id = _new_run_id()
    source_dir = get_settings().runs_dir / run_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    ingest_path = _save_uploads(files, source_dir)

    settings = get_settings()
    warehouse_connection_string: str | None = None
    if settings.client_warehouse_enabled:
        warehouse_connection_string = await asyncio.to_thread(
            _provision_and_get_source, run_id, ingest_path, source_dir, label
        )
        # Never let the raw credential reach RunRecord/the jobs DB/a log line -
        # same placeholder scheme create_run_from_path already uses for a
        # customer-supplied postgresql:// source.
        source_for_run = prepare_source_for_persistence(warehouse_connection_string)
    else:
        source_for_run = str(ingest_path)

    output_dir = str(get_settings().runs_dir / run_id / "output")
    ctx = await pipeline_runner.start_run(
        run_id,
        source_for_run,
        output_dir,
        industry_override=industry,
        use_llm=use_llm,
        use_agent=use_agent,
        label=label,
        tenant_id=user.email,
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


def _provision_and_get_source(
    run_id: str, ingest_path: Path, source_dir: Path, label: str | None = None
) -> str:
    """Loads the upload into the warehouse and returns the raw connection
    string - the caller immediately passes this through
    `prepare_source_for_persistence` before it touches `RunRecord`, exactly
    like a customer-supplied live-database source already works today.

    `label`, if the caller supplied one, wins over the auto-derived-from-
    filename default - resolved once, up front, so the except branch's
    deprovision call always targets the exact same schema name the
    provision call above it used.

    Runs on a thread (it makes a real network connection) and best-effort
    rolls back a partial schema if anything fails partway through."""
    settings = get_settings()
    assert settings.client_warehouse_url and settings.client_warehouse_public_host  # checked by caller
    label = label or _label_for_upload(ingest_path)
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


def _is_user_admin(user: UserORM | None) -> bool:
    if user is None:
        return False
    settings = get_settings()
    return bool(user.is_admin or user.email in settings.admin_email_list or user.email.startswith("admin@"))


@router.get("", response_model=list[RunSummary])
async def list_runs(
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
    scope: str = Query("all", description="'all' (all platform runs for admins) or 'mine'"),
) -> list[RunSummary]:
    is_admin = _is_user_admin(user)

    query = select(RunORM)
    if not is_admin or scope == "mine":
        # Regular users ONLY see their own runs and pending generation
        query = query.where(RunORM.tenant_id == user.email)

    query = query.order_by(RunORM.created_at.desc())
    result = await session.execute(query)
    rows = result.scalars().all()
    summaries: list[RunSummary] = []
    for r in rows:
        label = None
        industry = r.industry_override
        tables: list[str] = []
        kpis_count: int | None = None
        if r.record_json:
            label = r.record_json.get("label") or None
            for e in r.record_json.get("events", []):
                stage = e.get("stage")
                data = e.get("data", {})
                if stage == "ingest" and "tables" in data:
                    tables = data["tables"]
                elif stage == "classify" and not industry and data.get("ranked_matches"):
                    matches = data["ranked_matches"]
                    if matches:
                        industry = matches[0].get("pack_slug")
                elif stage == "compile_kpis" and "agent_proposed" in data:
                    kpis_count = len(data.get("agent_proposed", []))

        summaries.append(
            RunSummary(
                run_id=r.run_id,
                status=r.status,
                current_stage=r.current_stage,
                error=r.error,
                created_at=r.created_at.isoformat() if r.created_at else None,
                updated_at=r.updated_at.isoformat() if r.updated_at else None,
                label=label,
                industry=industry,
                tables=tables,
                kpis_count=kpis_count,
                tenant_id=r.tenant_id if is_admin else None,
                total_tokens=r.total_tokens or 0,
                llm_calls=r.llm_calls or 0,
            )
        )
    return summaries


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> RunDetail:
    return RunDetail.model_validate((await _load_record(run_id, session, user)).model_dump())


@router.get("/{run_id}/logs")
async def get_run_logs(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> dict:
    """Returns the consolidated execution log and error traces for a specific run."""
    record = await _load_record(run_id, session, user)
    log_file = get_settings().runs_dir / run_id / "pipeline.log"
    log_content = ""
    if log_file.exists():
        try:
            log_content = log_file.read_text(encoding="utf-8")
        except Exception:
            pass

    return {
        "run_id": run_id,
        "status": record.status.value,
        "error": record.error,
        "events_count": len(record.events),
        "log_text": log_content,
    }


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    user: Annotated[UserORM, Depends(get_current_user)],
    after: int = Query(0, ge=0),
    session: SessionDep = None,
) -> StreamingResponse:
    await _load_record(run_id, session, user)  # 404/tenant-check early

    async def gen() -> Any:
        sent = after
        while True:
            ctx = registry.get(run_id)
            if ctx is None:
                # Not live in this process (e.g. after a restart) - emit what's on
                # disk once and close; there is nothing further to stream.
                record = await _load_record(run_id, session, user)
                for event in record.events[sent:]:
                    yield _sse(event.model_dump(mode="json"))
                yield _sse({"final": True, "status": record.status.value})
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


@router.post("/{run_id}/review", response_model=RunSummary)
async def submit_review(
    run_id: str,
    body: ReviewRequest,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> RunSummary:
    """Resolves a NEEDS_INPUT run paused on data-quality questions (and/or an
    ambiguous industry match) in one call: sets `industry_override` if given,
    records `data_answers`, then resumes. `answers={}` is meaningful - it
    means "reviewed, nothing supplied" and is what stops the pause re-firing,
    exactly like `industry_override` does for classification."""
    ctx = await _require_live_context(run_id, session, user)
    _require_status(ctx.record, RunStatus.NEEDS_INPUT)
    if body.industry is not None:
        ctx.record.industry_override = body.industry
    ctx.record.data_answers = body.answers
    await pipeline_runner.resume_run(ctx)
    return _summary(ctx.record)


@router.post("/{run_id}/confirm-industry", response_model=RunSummary)
async def confirm_industry(
    run_id: str,
    body: ConfirmIndustryRequest,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> RunSummary:
    """Thin alias for `POST /review` that only supplies an industry - kept as
    the documented contract for the wizard's industry picker. Delegates with
    `answers={}` (or preserves an existing dict) so that resuming here can
    never immediately re-pause on `needs_answers`."""
    ctx = await _require_live_context(run_id, session, user)
    _require_status(ctx.record, RunStatus.NEEDS_INPUT)
    ctx.record.industry_override = body.industry
    ctx.record.data_answers = ctx.record.data_answers or {}
    await pipeline_runner.resume_run(ctx)
    return _summary(ctx.record)


@router.post("/{run_id}/confirm-bindings", response_model=RunSummary)
async def confirm_bindings(
    run_id: str,
    body: BindingConfirmationRequest,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> RunSummary:
    """Resolves a NEEDS_INPUT run paused on P1-08's binding gate
    (`record.binding_questions`). `confirmations={}` is meaningful - it
    declines every gated binding (the dependent KPIs land in .skipped with a
    reason) and is what stops the pause re-firing, exactly like
    `ReviewRequest.answers={}` does for data-quality questions."""
    ctx = await _require_live_context(run_id, session, user)
    _require_status(ctx.record, RunStatus.NEEDS_INPUT)
    ctx.record.binding_confirmations = body.confirmations
    await pipeline_runner.resume_run(ctx)
    return _summary(ctx.record)


@router.post("/{run_id}/bindings", response_model=RunSummary)
async def set_binding_overrides(
    run_id: str,
    body: BindingOverridesRequest,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> RunSummary:
    ctx = await _require_live_context(run_id, session, user)
    if ctx.record.status not in (RunStatus.NEEDS_INPUT, RunStatus.SUCCEEDED, RunStatus.FAILED):
        raise HTTPException(409, f"Cannot set binding overrides while run is {ctx.record.status.value}.")
    ctx.binding_overrides.update(body.overrides)
    await pipeline_runner.resume_run(ctx)
    return _summary(ctx.record)


@router.post("/{run_id}/cancel", response_model=RunSummary)
async def cancel_run(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> RunSummary:
    """Stops/cancels a running or paused run and marks it as failed."""
    # Ensure permission / existence
    await _load_record(run_id, session, user)
    record = await pipeline_runner.cancel_run(run_id, reason="Cancelled by user")
    return _summary(record)


@router.delete("/{run_id}")
async def delete_run(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> dict:
    """Deletes a run record from history and removes associated local directories."""
    await _load_record(run_id, session, user)
    # Stop if running
    try:
        await pipeline_runner.cancel_run(run_id, reason="Deleted by user")
    except Exception:
        pass

    # Remove from DB
    row = await session.get(RunORM, run_id)
    if row is not None:
        await session.delete(row)
        await session.commit()

    # Remove run artifacts from disk
    run_dir = get_settings().runs_dir / run_id
    if run_dir.exists():
        try:
            shutil.rmtree(run_dir, ignore_errors=True)
        except Exception:
            pass

    return {"ok": True, "run_id": run_id}


@router.get("/{run_id}/report")
async def get_report(run_id: str, session: SessionDep) -> dict:
    record = await _load_record(run_id, session)
    event = _last_event(record, RunStage.VALIDATE)
    if event is None or "report" not in event.data:
        raise HTTPException(404, "No validation report yet for this run.")
    return event.data["report"]


@router.get("/{run_id}/download")
async def download_plugin(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> FileResponse:
    record = await _load_record(run_id, session, user)
    event = _last_event_with(record, RunStage.PACKAGE, "plugin_dir")
    if event is None:
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
    run_id: str,
    body: PublishGithubRequest,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)],
) -> PublishGithubResponse:
    """Creates a brand-new GitHub repo for this run's packaged plugin and
    pushes it - the repo is immediately installable in Claude Code/Desktop
    with the two commands this returns. Only available once validation has
    passed (RunStatus.SUCCEEDED); see forge_core.publishing.standalone_repo."""
    record = await _load_record(run_id, session, user)
    if record.status != RunStatus.SUCCEEDED:
        raise HTTPException(
            409, f"Run is {record.status.value}; publish is only available after a successful run."
        )
    event = _last_event_with(record, RunStage.PACKAGE, "plugin_dir")
    if event is None:
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
        created_at=record.created_at.isoformat() if hasattr(record, "created_at") and record.created_at else None,
        label=getattr(record, "label", None),
        industry=getattr(record, "industry_override", None),
        total_tokens=record.token_usage.total_tokens,
        llm_calls=record.token_usage.llm_calls,
    )


def _last_event(record: RunRecord, stage: RunStage) -> StageEvent | None:
    for event in reversed(record.events):
        if event.stage == stage:
            return event
    return None


def _last_event_with(record: RunRecord, stage: RunStage, key: str) -> StageEvent | None:
    """Last event of `stage` that actually carries `key`.

    A stage emits several events and only some carry a given payload - PACKAGE
    logs "Packaging plugin", the packaged path, and the run's token usage. A
    plain last-event-of-stage lookup silently resolves to whichever happens to
    be last, so adding any new event to a stage broke `download` and `publish`
    with a 404. Selecting on the payload makes the lookup say what it means."""
    for event in reversed(record.events):
        if event.stage == stage and key in event.data:
            return event
    return None


def _require_status(record: RunRecord, expected: RunStatus) -> None:
    if record.status != expected:
        raise HTTPException(409, f"Run is {record.status.value}, expected {expected.value}.")


async def _require_live_context(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)] | None = None,
) -> registry.RunContext:
    """Returns the live RunContext for a run the current user owns. Falls back
    to DB rehydration so a paused run can always be resumed even after an API
    restart — no more permanent 409 on process death."""
    # Ownership check (404, not 403, to avoid user enumeration)
    await _load_record(run_id, session, user)
    ctx = registry.get(run_id)
    if ctx is not None:
        return ctx
    # Rehydrate from DB
    row = await session.get(RunORM, run_id)
    if row is None:  # guard (already checked by _load_record, but be safe)
        raise HTTPException(404, f"No run with id {run_id!r}.")
    record = RunRecord.model_validate(row.record_json)
    ctx = registry.RunContext(
        record=record,
        binding_overrides=dict(row.binding_overrides_json or {}),
        use_llm=bool(row.use_llm),
        use_agent=bool(row.use_agent),
    )
    registry.put(run_id, ctx)
    return ctx


async def _load_record(
    run_id: str,
    session: SessionDep,
    user: Annotated[UserORM, Depends(get_current_user)] | None = None,
) -> RunRecord:
    """Load RunRecord from registry (live) or DB. When `user` is provided,
    checks tenant ownership and 404s on mismatch (never 403 — see user enumeration).
    Admins are permitted to load any run."""
    is_admin = _is_user_admin(user)
    ctx = registry.get(run_id)
    if ctx is not None:
        # Tenant check against the in-memory record
        if user is not None and not is_admin:
            tenant = getattr(ctx.record, "tenant_id", None)
            if tenant != user.email:
                raise HTTPException(404, f"No run with id {run_id!r}.")
        return ctx.record

    row = await session.get(RunORM, run_id)
    if row is None:
        raise HTTPException(404, f"No run with id {run_id!r}.")
    if user is not None and not is_admin and row.tenant_id != user.email:
        raise HTTPException(404, f"No run with id {run_id!r}.")
    return RunRecord.model_validate(row.record_json)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
