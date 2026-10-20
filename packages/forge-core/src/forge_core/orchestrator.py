"""The stage state machine: ingest -> profile -> classify -> bind ->
compile_kpis -> generate -> package -> validate. Used identically by the
CLI (`forge run`, in-process, synchronous) and the API (wrapped in a
background task, persisted via `RunRecord`) - see docs/architecture.md for
the full pipeline diagram.

`run_pipeline` mutates and returns the `RunRecord` it's given, logging one
`StageEvent` per stage so both callers can render progress identically. It
pauses (status=NEEDS_INPUT) right after CLASSIFY when the top industry match
is below the auto-accept threshold and no override was supplied; the caller
sets `record.industry_override` and calls `run_pipeline` again to continue.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import classify, load_all_packs, load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.postgres import redact as redact_connection_string
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMProvider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.quality import DataReview
from forge_core.models.run import RunRecord
from forge_core.models.schema_model import SchemaModel
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.profiling import build_schema_profile
from forge_core.profiling.budget import AGENT_SCHEMA_BUDGET, FAST_AGENT_SCHEMA_BUDGET
from forge_core.profiling.quality import build_data_review
from forge_core.profiling.synthesis import build_schema_model
from forge_core.runtime_session import open_session
from forge_core.validation import run_harness

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PACKS_ROOT = REPO_ROOT / "industry-packs"


def run_pipeline(
    record: RunRecord,
    *,
    packs_root: Path = DEFAULT_PACKS_ROOT,
    profiling_provider: LLMProvider | None = None,
    generation_provider: LLMProvider | None = None,
    critique_provider: LLMProvider | None = None,
    binding_overrides: dict[str, str] | None = None,
    use_agent: bool = False,
    agent_schema: bool = False,
    fast: bool = False,
) -> RunRecord:
    # The understanding phase normally requires a live model: profiling
    # synthesis, generation prose, and self-critique all use one. Fail fast
    # and loud rather than silently degrading - UNLESS the operator has opted
    # into a degraded run with FORGE_LLM_DEGRADED_OK, in which case a missing
    # provider is allowed and the pipeline produces structural docs, template
    # prose, and no verified cookbook (every stage already degrades per-call).
    if profiling_provider is None or generation_provider is None or critique_provider is None:
        if os.environ.get("FORGE_LLM_DEGRADED_OK", "").lower() not in ("1", "true", "yes"):
            record.status = RunStatus.FAILED
            record.error = (
                "MIS Plugin Forge requires an LLM provider for the understanding phase. "
                "Set GEMINI_API_KEY (or ANTHROPIC_API_KEY + FORGE_LLM_PROVIDER=anthropic), or "
                "FORGE_LLM_CASSETTE_MODE=replay, or FORGE_LLM_DEGRADED_OK=true for a reduced run."
            )
            record.log(RunStage.INGEST, record.error)
            return record
        record.log(
            RunStage.PROFILE,
            "LLM unavailable - degraded run: structural docs only, template prose, no verified cookbook.",
        )

    record.status = RunStatus.RUNNING
    try:
        _run_pipeline_inner(
            record,
            packs_root=packs_root,
            profiling_provider=profiling_provider,
            generation_provider=generation_provider,
            critique_provider=critique_provider,
            binding_overrides=binding_overrides,
            use_agent=use_agent,
            agent_schema=agent_schema,
            fast=fast or os.environ.get("FORGE_AGENT_SCHEMA_FAST", "").lower() in ("1", "true", "yes"),
        )
    except Exception as exc:  # the orchestrator must never raise past the caller
        record.status = RunStatus.FAILED
        record.error = str(exc)
        record.log(record.current_stage or RunStage.INGEST, f"Run failed: {exc}")
    return record


def _read_catalog_for(record: RunRecord, data_source):  # type: ignore[no-untyped-def]
    """Best-effort declared-constraint read for agent_schema mode. Any failure
    (unsupported source, unreachable catalog, permission) logs and returns None
    so the run falls back to today's purely heuristic relationship detection."""
    from forge_core.ingestion.catalog import read_catalog  # noqa: PLC0415
    from forge_core.ingestion.postgres import extract_schema  # noqa: PLC0415
    from forge_core.ingestion.registry import _resolve_placeholder  # noqa: PLC0415

    record.log(RunStage.INGEST, "agent_schema: reading catalog (information_schema / PRAGMA)")
    try:
        is_pg = data_source.kind.value in ("postgres", "postgresql")
        schema = "public"
        if is_pg:
            try:
                schema = extract_schema(_resolve_placeholder(record.source_path)) or "public"
            except Exception:  # noqa: BLE001 - fall back to public if the URL can't be resolved
                schema = "public"
        con = open_session(data_source)
        try:
            return read_catalog(
                con, source_kind=data_source.kind, catalog_alias="srcdb", schema=schema
            )
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - catalog read is strictly best-effort
        record.log(RunStage.INGEST, f"Catalog read skipped (non-fatal): {exc}")
        return None


def _run_pipeline_inner(
    record: RunRecord,
    *,
    packs_root: Path,
    profiling_provider: LLMProvider | None,
    generation_provider: LLMProvider | None,
    critique_provider: LLMProvider | None,
    binding_overrides: dict[str, str] | None,
    use_agent: bool = False,
    agent_schema: bool = False,
    fast: bool = False,
) -> None:
    if record.profile is not None:
        # Resume after a NEEDS_INPUT pause: reuse the profile from the first
        # pass. Re-ingesting + re-profiling here is pure waste - and on a
        # remote database it's minutes of query traffic every time the caller
        # answers a question or confirms an industry. The DataSource rides
        # along on the profile (`.source`).
        profile = record.profile
        data_source = profile.source
        record.log(
            RunStage.PROFILE,
            f"Reusing profile from the first pass ({len(profile.structural.columns)} column(s))",
        )
    else:
        record.log(RunStage.INGEST, f"Ingesting {redact_connection_string(record.source_path)}")
        # Pass the raw string, not Path(record.source_path) - a live-database
        # connection string must reach registry.ingest() unmangled (Path()
        # would rewrite its "//" and turn "/" into "\" on Windows).
        data_source = ingest(record.source_path)
        record.log(
            RunStage.INGEST,
            f"Ingested {len(data_source.tables)} table(s)",
            tables=[t.name for t in data_source.tables],
        )

        # agent_schema mode: read declared FK/PK facts from information_schema
        # BEFORE profiling so the structural pass can use them.
        catalog = _read_catalog_for(record, data_source) if agent_schema else None
        if catalog is not None:
            data_source.catalog = catalog  # type: ignore[attr-defined]
            record.log(
                RunStage.INGEST,
                f"Catalog: {len(catalog.foreign_keys)} declared FK(s), "
                f"{len(catalog.primary_keys)} PK table(s), {len(catalog.comments)} comment(s)",
            )

        mode_tag = " [agent_schema" + (", fast" if fast else "") + " mode]" if agent_schema else ""
        record.log(RunStage.PROFILE, "Profiling schema" + mode_tag)
        if not agent_schema:
            _budget = None
        elif fast:
            _budget = FAST_AGENT_SCHEMA_BUDGET
        else:
            _budget = AGENT_SCHEMA_BUDGET
        profile = build_schema_profile(
            data_source, profiling_provider, catalog=catalog, budget=_budget
        )
        record.profile = profile
        record.log(RunStage.PROFILE, "Profile complete", columns=len(profile.structural.columns))

    # Data-quality review first - its deterministic findings feed the schema
    # synthesis below. Wrapped in its own try/except: a data-quality review
    # must inform, never block, so it can never be what turns this whole run
    # into a FAILED one. Computed once, reused on every resume.
    if record.data_review is None and fast:
        # Fast mode skips the deterministic quality analysis (per-table
        # reservoir sampling + top-value scans) - another round-trip per table.
        record.data_review = DataReview(generated_at=datetime.now(UTC).isoformat())
        record.log(RunStage.PROFILE, "Data-quality review skipped (fast mode)")
    elif record.data_review is None:
        con = open_session(data_source)
        try:
            record.data_review = build_data_review(
                data_source,
                profile.structural,
                con,
                provider=profiling_provider if record.data_answers is None else None,
                semantic=profile.semantic,
            )
        except Exception as exc:  # noqa: BLE001 - informs, never blocks
            record.data_review = DataReview(generated_at=datetime.now(UTC).isoformat())
            record.log(RunStage.PROFILE, f"Data-quality review unavailable: {exc}")
        finally:
            con.close()
    record.log(
        RunStage.PROFILE,
        f"Data quality: {len(record.data_review.findings)} finding(s)",
        review=record.data_review.model_dump(mode="json"),
    )

    # Pause for the user to clarify what the data means, before synthesis (so
    # their answers actually shape the knowledge pack). `data_answers is None`
    # = not asked yet; `{}` = asked and the caller opted out - either way, on
    # resume we fall through. Mirrors the industry-confirmation pause below.
    if record.data_answers is None and record.data_review.questions:
        record.status = RunStatus.NEEDS_INPUT
        record.log(
            RunStage.PROFILE,
            f"Awaiting {len(record.data_review.questions)} data clarification(s) from the caller",
            questions=[q.model_dump(mode="json") for q in record.data_review.questions],
        )
        return

    user_context = record.data_review.to_context(record.data_answers or {})["notes"]

    # Semantic synthesis - the mandatory LLM pass that turns structural facts
    # + quality findings + the user's own clarifications into the knowledge
    # pack the plugin ships. Computed once, reused on resume (cached on the
    # record and on disk by schema hash). A model hiccup degrades to a
    # sparser SchemaModel, not a failure.
    if record.schema_model is None:
        try:
            record.schema_model = build_schema_model(
                profile.structural,
                data_source,
                profiling_provider,
                quality_findings=record.data_review.findings,
                user_context=user_context,
            )
            sm = record.schema_model
            verb = "Reused cached" if sm.from_cache else "Synthesized"
            fail_note = (
                f", {len(sm.synthesis_failures)} table failure(s): {sm.synthesis_failures}"
                if sm.synthesis_failures
                else ""
            )
            record.log(
                RunStage.PROFILE,
                f"{verb} schema model: {len(sm.tables)} table doc(s), "
                f"{sum(len(t.columns) for t in sm.tables)} column doc(s), "
                f"{len(sm.cookbook)}/{sm.coverage.get('cookbook_total', 0)} verified cookbook quer(ies), "
                f"{len(sm.patterns)} pattern note(s), coverage {sm.schema_coverage():.0%}{fail_note}",
            )
        except Exception as exc:  # noqa: BLE001 - degrade, don't crash the run
            record.schema_model = SchemaModel(schema_hash="", generated_by="unavailable")
            record.log(RunStage.PROFILE, f"Schema-model synthesis failed: {exc}")

    record.log(RunStage.CLASSIFY, "Classifying industry")
    packs = load_all_packs(packs_root)
    classification = classify(profile, packs)
    record.log(
        RunStage.CLASSIFY,
        f"Top match: {classification.primary_pack_slug} "
        f"({classification.ranked_matches[0].confidence:.2f})",
        ranked_matches=[m.model_dump() for m in classification.ranked_matches],
        requires_customer_confirmation=classification.requires_customer_confirmation,
    )

    if classification.requires_customer_confirmation and not record.industry_override:
        record.status = RunStatus.NEEDS_INPUT
        record.log(RunStage.CLASSIFY, "Awaiting industry confirmation from caller")
        return

    pack_slug = record.industry_override or classification.primary_pack_slug
    pack = load_pack(packs_root / pack_slug)

    record.log(RunStage.BIND, f"Binding schema to {pack.slug}" + (" (agent-assisted)" if use_agent else ""))
    bindings = resolve_bindings(profile, pack, profiling_provider, binding_overrides, use_agent=use_agent)
    record.log(
        RunStage.BIND,
        f"Bound {len(bindings.columns)} role(s); {len(bindings.unresolved_roles)} unresolved",
        unresolved_roles=bindings.unresolved_roles,
        agent_bound_roles=[c.role for c in bindings.columns if c.source == "agent_proposed"],
    )

    record.log(RunStage.COMPILE_KPIS, "Compiling KPIs")
    kpi_defs = compile_all(pack, bindings)
    record.log(
        RunStage.COMPILE_KPIS,
        f"Compiled {len(kpi_defs.kpis)}/{len(pack.kpis)} KPI(s)",
        skipped=kpi_defs.skipped,
    )

    record.log(RunStage.GENERATE, "Generating plugin content")
    generated = generate_plugin_content(pack, kpi_defs, data_source, generation_provider)
    record.log(RunStage.GENERATE, "Generation complete", commands=[c.name for c in generated.commands])

    record.log(RunStage.PACKAGE, "Packaging plugin")
    spec = build_plugin_spec(
        pack, profile, bindings, kpi_defs, generated,
        schema_model=record.schema_model, customer_label=record.label,
    )
    plugin_dir = Path(record.output_dir) / spec.manifest.name
    write_plugin(
        spec, plugin_dir, source=data_source, profile=profile, pack=pack,
        schema_model=record.schema_model, agent_schema=agent_schema,
    )
    record.log(RunStage.PACKAGE, f"Packaged to {plugin_dir}", plugin_dir=str(plugin_dir))

    record.log(RunStage.VALIDATE, "Running validation harness (8 checks)")
    report = run_harness(
        pack=pack,
        profile=profile,
        bindings=bindings,
        kpi_defs=kpi_defs,
        generated=generated,
        provider=critique_provider,
        schema_model=record.schema_model,
        plugin_dir=plugin_dir,
        config_dir=plugin_dir / "config",
        data_dir=plugin_dir / "data",
        on_check=lambda result: record.log(
            RunStage.VALIDATE,
            f"{result.check}: {result.status.value}",
            check=result.check,
            status=result.status.value,
        ),
    )
    record.log(
        RunStage.VALIDATE,
        f"Validation overall: {report.overall.value}",
        overall=report.overall.value,
        report=report.model_dump(mode="json"),
    )

    if report.overall == "fail":
        record.status = RunStatus.FAILED
        record.error = "Validation harness reported hard failures; see the validate stage event."
    else:
        record.status = RunStatus.SUCCEEDED


__all__ = ["DEFAULT_PACKS_ROOT", "run_pipeline"]
