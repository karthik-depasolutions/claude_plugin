"""Bridges the synchronous `forge_core.orchestrator.run_pipeline` into the
async API: runs it in a worker thread via `asyncio.to_thread`, then mirrors
the resulting `RunRecord` into the `runs` table. Starting or resuming a run
both go through `_execute` — resuming just means `run_pipeline` is invoked
again on the same `RunRecord` with `industry_override`/`binding_overrides`
now set, which is exactly how the orchestrator is designed to be driven."""

from __future__ import annotations

import asyncio

from forge_core.llm import get_provider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline

from forge_api import registry
from forge_api.db import session_factory
from forge_api.models_orm import RunORM


async def start_run(
    run_id: str, source_path: str, output_dir: str, *, industry_override: str | None, use_llm: bool
) -> registry.RunContext:
    record = RunRecord(
        run_id=run_id, source_path=source_path, output_dir=output_dir, industry_override=industry_override
    )
    ctx = registry.RunContext(record=record)
    registry.put(run_id, ctx)
    await _persist(ctx)
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
