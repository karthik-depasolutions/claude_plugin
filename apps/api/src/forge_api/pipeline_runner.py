"""Bridges the synchronous `forge_core.orchestrator.run_pipeline` into the
async API: runs it in a worker thread via `asyncio.to_thread`, then mirrors
the resulting `RunRecord` into the `runs` table. Starting or resuming a run
both go through `_execute` — resuming just means `run_pipeline` is invoked
again on the same `RunRecord` with `industry_override`/`binding_overrides`
now set, which is exactly how the orchestrator is designed to be driven.

Persistence strategy:
  - `_persist` is called at start and at end (terminal/paused states).
  - `_persist_on_event` is registered as an on_event callback so every
    StageEvent is mirrored to the DB as it happens. This prevents losing
    mid-run progress on a crash and lets /events replay from DB on resume.
  - `use_llm`/`use_agent` are written to RunORM at start so rehydration
    after an API restart picks up the correct flags (see routers/runs.py).
"""

from __future__ import annotations

import asyncio
import logging

from forge_core.llm import get_provider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord, StageEvent
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline

from forge_api import registry
from forge_api.db import session_factory
from forge_api.models_orm import RunORM

from datetime import UTC, datetime
from pathlib import Path

# A dedicated, explicitly-configured logger rather than relying on the root
# logger: uvicorn only sets up handlers on its own "uvicorn"/"uvicorn.access"
# loggers, so plain `logging.getLogger(__name__).info(...)` would otherwise
# be silently dropped (root's default level is WARNING, no handler attached).
logger = logging.getLogger("forge_api.pipeline")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_handler)

    try:
        _log_dir = Path("generated/logs")
        _log_dir.mkdir(parents=True, exist_ok=True)
        _file_handler = logging.FileHandler(_log_dir / "forge.log", encoding="utf-8")
        _file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(_file_handler)
    except Exception:
        pass

    logger.setLevel(logging.INFO)
    logger.propagate = False


def _log_event(run_id: str, event: StageEvent) -> None:
    logger.info("[%s] %-13s %s", run_id, event.stage.value, event.message)
    try:
        run_log_path = Path("generated/runs") / run_id / "pipeline.log"
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        with run_log_path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(UTC).isoformat()} [{event.stage.value}] {event.message}\n")
    except Exception:
        pass


async def start_run(
    run_id: str,
    source_path: str,
    output_dir: str,
    *,
    industry_override: str | None,
    use_llm: bool,
    use_agent: bool = True,
    label: str | None = None,
    tenant_id: str = "_local",
) -> registry.RunContext:
    record = RunRecord(
        run_id=run_id,
        source_path=source_path,
        output_dir=output_dir,
        industry_override=industry_override,
        tenant_id=tenant_id,
        label=label,
    )
    record.on_event(lambda event: _log_event(run_id, event))
    ctx = registry.RunContext(record=record)
    ctx.use_agent = use_agent
    ctx.use_llm = use_llm
    registry.put(run_id, ctx)
    await _persist(ctx)
    logger.info("[%s] started - source=%s industry=%s use_llm=%s", run_id, source_path, industry_override, use_llm)
    # Register per-event persistence AFTER the initial _persist so the first
    # event doesn't race with the row INSERT.
    loop = asyncio.get_event_loop()

    def _schedule_persist(event: StageEvent, _ctx: registry.RunContext = ctx) -> None:
        """Hand the write from the pipeline thread back to the API's loop.

        The pipeline runs on its own thread and outlives the loop in two real
        situations: an API shutting down mid-run, and a test whose event loop
        closes while a run is still going. `call_soon_threadsafe` on a closed
        loop raises RuntimeError inside the *pipeline* thread, where nothing
        catches it - it surfaced as a cross-suite flake ("Event loop is
        closed") that only appeared when another suite's teardown happened to
        land in the window. Losing a persist during shutdown is fine (the
        terminal `_persist` in `_execute`'s finally block is the durable
        write); crashing the pipeline thread over it is not."""
        try:
            loop.call_soon_threadsafe(
                lambda e=event: asyncio.ensure_future(_persist_on_event(_ctx, e))
            )
        except RuntimeError as exc:  # loop already closed / shutting down
            logger.debug("[%s] skipped per-event persist: %s", _ctx.record.run_id, exc)

    record.on_event(_schedule_persist)
    ctx.task = asyncio.create_task(_execute(ctx, use_llm=use_llm))  # noqa: RUF006 - fire-and-forget job, tracked via ctx
    return ctx


async def resume_run(ctx: registry.RunContext) -> None:
    # `use_llm` is a run-level choice persisted on the context at start -
    # resuming must not silently flip an LLM-free run into an LLM one
    # (previously hardcoded `use_llm=True` here, which made a `--no-llm`
    # run acquire LLM-written prose on resume).
    ctx.task = asyncio.create_task(_execute(ctx, use_llm=ctx.use_llm))  # noqa: RUF006


async def cancel_run(run_id: str, reason: str = "Cancelled by user") -> RunRecord:
    """Stops/cancels a running or paused run and marks it as FAILED with the cancellation reason."""
    ctx = registry.get(run_id)
    if ctx is not None:
        if ctx.task and not ctx.task.done():
            ctx.task.cancel()
        ctx.running = False
        ctx.record.status = RunStatus.FAILED
        ctx.record.error = reason
        ctx.record.log(ctx.record.current_stage or RunStage.INGEST, f"Run cancelled: {reason}")
        await _persist(ctx)
        logger.info("[%s] Cancelled active run in memory: %s", run_id, reason)
        return ctx.record

    # If not active in memory (e.g., zombie after server restart), update DB row directly
    async with session_factory()() as session:
        row = await session.get(RunORM, run_id)
        if row is None:
            raise ValueError(f"Run {run_id} not found")
        row.status = RunStatus.FAILED.value
        row.error = reason
        if row.record_json:
            row.record_json["status"] = RunStatus.FAILED.value
            row.record_json["error"] = reason
            events = row.record_json.get("events", [])
            events.append({
                "stage": row.current_stage or "ingest",
                "message": f"Run cancelled: {reason}",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {},
            })
            row.record_json["events"] = events
        await session.commit()
        logger.info("[%s] Cancelled persisted run in database: %s", run_id, reason)
        return RunRecord.model_validate(row.record_json)


async def startup_cleanup_zombie_runs() -> None:
    """Marks any runs left in RUNNING status from a previous crashed/reloaded process as FAILED."""
    from sqlalchemy import select
    try:
        async with session_factory()() as session:
            result = await session.execute(
                select(RunORM).where(RunORM.status == RunStatus.RUNNING.value)
            )
            zombies = result.scalars().all()
            for z in zombies:
                z.status = RunStatus.FAILED.value
                z.error = "Pipeline execution was stopped due to server restart or interruption."
                if z.record_json:
                    z.record_json["status"] = RunStatus.FAILED.value
                    z.record_json["error"] = z.error
                    events = z.record_json.get("events", [])
                    events.append({
                        "stage": z.current_stage or "ingest",
                        "message": "Run stopped: Server process restarted while execution was in progress.",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "data": {},
                    })
                    z.record_json["events"] = events
            if zombies:
                logger.info("Cleaned up %d orphaned zombie run(s) from prior session", len(zombies))
                await session.commit()
    except Exception as exc:
        logger.warning("Startup cleanup of zombie runs failed: %s", exc)


import os

async def _execute_temporal(ctx: registry.RunContext) -> bool:
    """Attempts to execute pipeline via Temporal workflow. Returns True if succeeded."""
    record = ctx.record
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    try:
        from temporalio.client import Client
        from workers.temporal_worker.workflows.forge_generation import (
            TASK_QUEUE,
            ForgeGenerationWorkflow,
            ForgeWorkflowInput,
        )

        client = await Client.connect(temporal_address)
        input_data = ForgeWorkflowInput(
            run_id=record.run_id,
            tenant_id=getattr(record, "tenant_id", "default"),
            source_path=record.source_path,
            output_dir=record.output_dir,
            industry_override=record.industry_override,
            data_answers=record.data_answers or {},
            binding_confirmations=record.binding_confirmations or {},
            use_agent=ctx.use_agent,
            label=record.label,
        )
        logger.info("[%s] Dispatching to Temporal workflow at %s", record.run_id, temporal_address)
        handle = await client.start_workflow(
            ForgeGenerationWorkflow.run,
            input_data,
            id=f"forge-{record.run_id}",
            task_queue=TASK_QUEUE,
        )
        result = await handle.result()
        if result.status == "succeeded":
            record.status = RunStatus.SUCCEEDED
            record.output_dir = result.plugin_dir or record.output_dir
        else:
            record.status = RunStatus.FAILED
            record.error = result.error
        return True
    except Exception as exc:
        logger.warning(
            "[%s] Temporal dispatch failed (%s). Falling back to in-process execution.",
            record.run_id,
            exc,
        )
        return False


async def _execute(ctx: registry.RunContext, *, use_llm: bool) -> None:
    ctx.running = True
    record = ctx.record
    try:
        if os.getenv("USE_TEMPORAL", "false").lower() in ("true", "1"):
            dispatched = await _execute_temporal(ctx)
            if dispatched:
                return

        profiling = get_provider(role="profiling") if use_llm else None
        generation = get_provider(role="generation") if use_llm else None
        critique = get_provider(role="critique") if use_llm else None
        await asyncio.to_thread(
            run_pipeline,
            record,
            packs_root=DEFAULT_PACKS_ROOT,
            profiling_provider=profiling,
            generation_provider=generation,
            critique_provider=critique,
            binding_overrides=ctx.binding_overrides or None,
            use_agent=ctx.use_agent,
        )
    except Exception as exc:
        import traceback

        tb = traceback.format_exc()
        logger.error("[%s] Pipeline execution failed: %s\n%s", record.run_id, exc, tb)
        try:
            run_log_path = Path("generated/runs") / record.run_id / "pipeline.log"
            run_log_path.parent.mkdir(parents=True, exist_ok=True)
            with run_log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n[ERROR] Pipeline failed: {exc}\n{tb}\n")
        except Exception:
            pass

        # Provider construction (e.g. a missing GEMINI_API_KEY) happens
        # *before* run_pipeline's own try/except is entered, so without this
        # the background task's exception would just vanish into "Task
        # exception was never retrieved" and the run would sit at "pending"
        # forever instead of surfacing as a failure the caller can see.
        record.status = RunStatus.FAILED
        record.error = str(exc)
        record.log(record.current_stage or RunStage.INGEST, f"Run failed to start: {exc}")
    finally:
        ctx.running = False
        await _persist(ctx)
        logger.info("[%s] finished - status=%s%s", record.run_id, record.status.value, f" ({record.error})" if record.error else "")


def _build_row(ctx: registry.RunContext) -> RunORM:
    """Build the RunORM row from current context state. Centralised so
    _persist and _persist_on_event both produce identical shapes."""
    record = ctx.record
    return RunORM(
        run_id=record.run_id,
        status=record.status.value,
        current_stage=record.current_stage.value if record.current_stage else None,
        source_path=record.source_path,
        output_dir=record.output_dir,
        industry_override=record.industry_override,
        error=record.error,
        record_json=record.model_dump(mode="json"),
        binding_overrides_json=ctx.binding_overrides,
        tenant_id=getattr(record, "tenant_id", "_local"),
        use_llm=ctx.use_llm,
        use_agent=ctx.use_agent,
        input_tokens=record.token_usage.input_tokens,
        output_tokens=record.token_usage.output_tokens,
        total_tokens=record.token_usage.total_tokens,
        llm_calls=record.token_usage.llm_calls,
    )


async def _persist(ctx: registry.RunContext) -> None:
    """Persist the full RunRecord at start and end (terminal/paused states)."""
    row = _build_row(ctx)
    async with session_factory()() as session:
        await session.merge(row)
        await session.commit()


async def _persist_on_event(ctx: registry.RunContext, _event: StageEvent) -> None:
    """Lightweight per-event upsert — mirrors status/stage/record_json to the
    DB every time a StageEvent fires. Called from a thread-safe fire-and-forget
    registered in start_run; any exception here is swallowed so a DB blip never
    kills the pipeline thread."""
    try:
        row = _build_row(ctx)
        async with session_factory()() as session:
            await session.merge(row)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] _persist_on_event failed: %s", ctx.record.run_id, exc)
