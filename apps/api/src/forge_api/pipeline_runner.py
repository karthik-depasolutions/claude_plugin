"""Bridges the synchronous `forge_core.orchestrator.run_pipeline` into the
async API: runs it in a worker thread via `asyncio.to_thread`, then mirrors
the resulting `RunRecord` into the `runs` table. Starting or resuming a run
both go through `_execute` — resuming just means `run_pipeline` is invoked
again on the same `RunRecord` with `industry_override`/`binding_overrides`
now set, which is exactly how the orchestrator is designed to be driven."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from forge_core.ingestion.credentials import set_credentials
from forge_core.llm import LLMError, LLMProvider, get_provider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord, StageEvent
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline

from forge_api import registry
from forge_api.config import Settings, get_settings
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
    use_agent: bool = False,
    agent_schema: bool = False,
    no_llm: bool = False,
    label: str | None = None,
    source_credentials: dict[str, str] | None = None,
    warehouse_schema: str | None = None,
    publish_agents_to_source: bool = False,
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
    ctx.agent_schema = agent_schema
    ctx.no_llm = no_llm
    ctx.publish_agents_to_source = publish_agents_to_source
    if no_llm:
        # Headless test run: there is no caller to answer the pre-synthesis
        # clarification questions, so take the "nothing to add" path (mirrors
        # `forge run --no-llm`), and it must be set before the first _persist.
        record.data_answers = {}
    ctx.source_credentials = source_credentials or {}
    ctx.warehouse_schema = warehouse_schema
    registry.put(run_id, ctx)
    await _persist(ctx)
    logger.info("[%s] started - source=%s industry=%s", run_id, source_path, industry_override)
    asyncio.create_task(_execute(ctx))  # noqa: RUF006 - fire-and-forget job, tracked via ctx
    return ctx


async def resume_run(ctx: registry.RunContext) -> None:
    asyncio.create_task(_execute(ctx))  # noqa: RUF006


async def _execute(ctx: registry.RunContext) -> None:
    ctx.running = True
    record = ctx.record
    usage = ctx.token_usage_acc  # one accumulator for the run, across pause/resume
    degraded_ok = os.environ.get("FORGE_LLM_DEGRADED_OK", "").lower() in ("1", "true", "yes")
    profiling: LLMProvider | None
    generation: LLMProvider | None
    critique: LLMProvider | None
    try:
        if ctx.no_llm:
            from forge_core.testing import FakeLLMProvider

            profiling = generation = critique = FakeLLMProvider()
        else:
            try:
                profiling = get_provider(role="profiling", usage=usage)
                generation = get_provider(role="generation", usage=usage)
                critique = get_provider(role="critique", usage=usage)
            except LLMError:
                if not degraded_ok:
                    raise
                profiling = generation = critique = None  # run_pipeline logs the degraded notice
        # Bind this run's source credential (a customer postgresql:// URL, or a
        # clone's scoped connection string) for the duration of the pipeline
        # thread only - never on os.environ, so a concurrent run against a
        # different database isn't affected. copy_context() inside to_thread
        # carries the ContextVar onto the worker thread.
        with set_credentials(ctx.source_credentials):
            await asyncio.to_thread(
                run_pipeline,
                record,
                packs_root=DEFAULT_PACKS_ROOT,
                profiling_provider=profiling,
                generation_provider=generation,
                critique_provider=critique,
                binding_overrides=ctx.binding_overrides or None,
                use_agent=ctx.use_agent,
                agent_schema=ctx.agent_schema,
                fast=ctx.no_llm,
            )
        if record.status == RunStatus.SUCCEEDED:
            _persist_hosted_source_env(ctx)
            await _publish_agents_schema_if_applicable(ctx)
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
        ctx.token_usage = usage.snapshot()
        await _persist(ctx)
        tu = ctx.token_usage
        logger.info(
            "[%s] finished - status=%s%s - llm %d in / %d out / %d total (%d calls)",
            record.run_id,
            record.status.value,
            f" ({record.error})" if record.error else "",
            tu["input_tokens"],
            tu["output_tokens"],
            tu["total_tokens"],
            tu["calls"],
        )


def _persist_hosted_source_env(ctx: registry.RunContext) -> None:
    """Write this run's source credential(s) to a server-side file next to
    `.mcp_token`, so the hosted (Pattern B) MCP gateway connects to *this*
    run's database. There is no process-global `FORGE_SOURCE_DB_URL` on the
    API - this file is the only thing the gateway has. No-op for a file
    source. Server-side only - never enters the packaged plugin, the zip, or
    the repo.
    """
    creds = ctx.source_credentials
    if not creds:
        return
    from forge_core.ingestion.postgres import normalize_pg_url
    from forge_core.packaging.plugin_builder import write_mcp_source_env

    resolved = {
        k: (normalize_pg_url(v) if "postgres" in v[:20].lower() else v) for k, v in creds.items()
    }
    write_mcp_source_env(Path(ctx.record.output_dir), resolved)


def _agents_publish_target(ctx: registry.RunContext, settings: Settings) -> tuple[str, str] | None:
    """`(connection_string, model_name)` for the AGENTS.* projection, or None
    when it shouldn't run for this run. Two mutually exclusive targets:

    - warehouse-backed run -> the Forge warehouse (unconditional, agent_schema
      only). Forge owns that database and always projects there.
    - live-database run + `publish_agents_to_source` opt-in -> the customer's
      *own* source database, so their other SQL agents can discover the schema.
      Needs the source credential to hold CREATE rights; best-effort past that.

    A file-upload run with no warehouse has no database to project into.
    """
    from forge_core.ingestion.postgres import CREDENTIAL_ENV_VAR, extract_schema

    if ctx.warehouse_schema and settings.client_warehouse_enabled:
        if not ctx.agent_schema:
            return None
        return settings.client_warehouse_url, ctx.warehouse_schema

    if ctx.publish_agents_to_source:
        source_url = ctx.source_credentials.get(CREDENTIAL_ENV_VAR)
        if source_url:
            return source_url, extract_schema(source_url)
    return None


async def _publish_agents_schema_if_applicable(ctx: registry.RunContext) -> None:
    """After a successful run, project the knowledge pack into the open
    `AGENTS.*` tables (dbt-labs/agents_schema shape) so any SQL agent can read
    it - on the Forge warehouse for a warehouse-backed run, or, opt-in, inside
    the customer's own source database (`publish_agents_to_source`). Strictly
    best-effort: the sharded config/catalog/ shipped in the plugin is the
    source of truth, and the generated plugin stays read-only regardless; a
    failure here never fails the run."""
    settings = get_settings()
    record = ctx.record
    target = _agents_publish_target(ctx, settings)
    if target is None or record.schema_model is None or not record.schema_model.tables:
        return
    connection_string, model_name = target
    try:
        from forge_core.publishing.agents_schema import publish_agents_schema

        kpi_defs = None
        kpi_path = Path(record.output_dir)
        for p in kpi_path.rglob("config/kpi_defs.json"):
            kpi_defs = json.loads(p.read_text(encoding="utf-8"))
            break
        n = await asyncio.to_thread(
            publish_agents_schema,
            connection_string,
            model_name=model_name,
            schema_model=record.schema_model.model_dump(mode="json"),
            kpi_defs=kpi_defs,
        )
        record.log(RunStage.PUBLISH, f"Published AGENTS.* projection ({n} statements)")
    except Exception as exc:  # noqa: BLE001 - interop nicety, never fatal
        record.log(RunStage.PUBLISH, f"AGENTS.* projection skipped (non-fatal): {exc}")


def _pack_fit(record: RunRecord) -> tuple[str | None, float | None, int, int]:
    """(primary_pack_slug, confidence, kpis_compiled, kpis_skipped) read off
    the CLASSIFY and COMPILE_KPIS events. The demand signal for pack authoring."""
    slug: str | None = None
    confidence: float | None = None
    compiled = skipped = 0
    for e in record.events:
        if e.stage is RunStage.CLASSIFY and e.data.get("ranked_matches"):
            top = e.data["ranked_matches"][0]
            slug, confidence = top.get("pack_slug"), top.get("confidence")
        if e.stage is RunStage.COMPILE_KPIS and "skipped" in e.data:
            skipped = len(e.data["skipped"])
            m = re.search(r"Compiled\s+(\d+)\s*/\s*(\d+)", e.message)
            compiled = int(m.group(1)) if m else 0
    return slug, confidence, compiled, skipped


def _knowledge_pack(record: RunRecord) -> tuple[float | None, float | None, bool | None]:
    """(cookbook_pass_rate, schema_coverage, cache_hit) off the synthesized
    schema model - how strong the shipped knowledge pack is."""
    sm = record.schema_model
    if sm is None or not sm.coverage:
        return None, None, None
    return sm.cookbook_pass_rate(), sm.schema_coverage(), sm.from_cache


def _record_json(record: RunRecord) -> dict:
    """`model_dump(mode="json")`, but never let one un-serializable leaf (a
    numpy scalar, a Decimal, a stray datetime) crash the persist and lose the
    whole run - a failure that otherwise only surfaces as an unretrieved
    asyncio task exception. Fall back to a python dump with `default=str` for
    the offenders, and log so it's noticed."""
    try:
        return record.model_dump(mode="json")
    except Exception:
        logger.warning("[%s] record_json: mode='json' dump failed, using str fallback", record.run_id)
        return json.loads(json.dumps(record.model_dump(mode="python"), default=str))


async def _persist(ctx: registry.RunContext) -> None:
    record = ctx.record
    tu = ctx.token_usage or {}
    slug, confidence, compiled, skipped = _pack_fit(record)
    cookbook_rate, coverage, cache_hit = _knowledge_pack(record)
    row = RunORM(
        run_id=record.run_id,
        status=record.status.value,
        current_stage=record.current_stage.value if record.current_stage else None,
        source_path=record.source_path,
        output_dir=record.output_dir,
        industry_override=record.industry_override,
        error=record.error,
        record_json=_record_json(record),
        binding_overrides_json=ctx.binding_overrides,
        llm_input_tokens=tu.get("input_tokens", 0),
        llm_output_tokens=tu.get("output_tokens", 0),
        llm_total_tokens=tu.get("total_tokens", 0),
        llm_calls=tu.get("calls", 0),
        llm_token_usage_json=tu or None,
        llm_estimated_cost_usd=tu.get("estimated_cost_usd"),
        primary_pack_slug=slug,
        pack_confidence=confidence,
        kpis_compiled=compiled,
        kpis_skipped=skipped,
        cookbook_pass_rate=cookbook_rate,
        schema_coverage=coverage,
        schema_model_cache_hit=cache_hit,
        warehouse_schema=ctx.warehouse_schema,
        warehouse_provisioned_at=datetime.now(UTC) if ctx.warehouse_schema else None,
    )
    async with session_factory()() as session:
        await session.merge(row)
        await session.commit()
    if slug is not None:
        logger.info(
            "[%s] pack-fit slug=%s confidence=%s generic=%s kpis=%d/%d",
            record.run_id, slug, f"{confidence:.2f}" if confidence is not None else "?",
            slug == "generic-analytics", compiled, compiled + skipped,
        )
