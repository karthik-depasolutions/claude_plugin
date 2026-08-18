"""Bridges the synchronous `forge_core.orchestrator.run_pipeline` into the
async API: runs it in a worker thread via `asyncio.to_thread`, then mirrors
the resulting `RunRecord` into the `runs` table. Starting or resuming a run
both go through `_execute` — resuming just means `run_pipeline` is invoked
again on the same `RunRecord` with `industry_override`/`binding_overrides`
now set, which is exactly how the orchestrator is designed to be driven."""

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

# A dedicated, explicitly-configured logger rather than relying on the root
# logger: uvicorn only sets up handlers on its own "uvicorn"/"uvicorn.access"
# loggers, so plain `logging.getLogger(__name__).info(...)` would otherwise
# be silently dropped (root's default level is WARNING, no handler attached).
# This is the only place run progress reaches the API process's own
# terminal - the SSE stream (`RunRecord.events`) is what the browser sees,
# this is what whoever is running the server sees.
logger = logging.getLogger("forge_api.pipeline")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _log_event(run_id: str, event: StageEvent) -> None:
    logger.info("[%s] %-13s %s", run_id, event.stage.value, event.message)


async def start_run(
    run_id: str,
    source_path: str,
    output_dir: str,
    *,
    industry_override: str | None,
    use_llm: bool,
    use_agent: bool = False,
    label: str | None = None,
) -> registry.RunContext:
    record = RunRecord(
        run_id=run_id,
        source_path=source_path,
        output_dir=output_dir,
        industry_override=industry_override,
        label=label,
    )
    record.on_event(lambda event: _log_event(run_id, event))
    ctx = registry.RunContext(record=record)
    ctx.use_agent = use_agent
    registry.put(run_id, ctx)
    await _persist(ctx)
    logger.info("[%s] started - source=%s industry=%s use_llm=%s", run_id, source_path, industry_override, use_llm)
    asyncio.create_task(_execute(ctx, use_llm=use_llm))  # noqa: RUF006 - fire-and-forget job, tracked via ctx
    return ctx


async def resume_run(ctx: registry.RunContext, *, use_llm: bool) -> None:
    asyncio.create_task(_execute(ctx, use_llm=use_llm))  # noqa: RUF006


async def _execute(ctx: registry.RunContext, *, use_llm: bool) -> None:
    ctx.running = True
    record = ctx.record
    try:
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


async def _persist(ctx: registry.RunContext) -> None:
    record = ctx.record
    row = RunORM(
        run_id=record.run_id,
        status=record.status.value,
        current_stage=record.current_stage.value if record.current_stage else None,
        source_path=record.source_path,
        output_dir=record.output_dir,
        industry_override=record.industry_override,
        error=record.error,
        record_json=record.model_dump(mode="json"),
        binding_overrides_json=ctx.binding_overrides,
    )
    async with session_factory()() as session:
        await session.merge(row)
        await session.commit()
