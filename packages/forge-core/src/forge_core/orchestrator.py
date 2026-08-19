"""The stage state machine: ingest -> profile -> classify -> bind ->
compile_kpis -> generate -> package -> validate. Used identically by the
CLI (`forge run`, in-process, synchronous) and the API (wrapped in a
background task, persisted via `RunRecord`) - see docs/architecture.md for
the full pipeline diagram.

`run_pipeline` mutates and returns the `RunRecord` it's given, logging one
`StageEvent` per stage so both callers can render progress identically. It
pauses (status=NEEDS_INPUT) right after CLASSIFY when there's anything the
caller should weigh in on: the top industry match is below the auto-accept
threshold with no override supplied, and/or the data-quality review raised
questions with no answers yet. One merged pause, never two. The caller sets
`record.industry_override` / `record.data_answers` and calls `run_pipeline`
again to continue - clean data with a confident classification never pauses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import classify, load_all_packs, load_pack
from forge_core.compiler import compile_all
from forge_core.compiler.kpi_compiler import KpiCompileError, compile_kpi
from forge_core.compiler.kpi_proposer import propose_kpis
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.postgres import redact as redact_connection_string
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMProvider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.quality import DataReview
from forge_core.models.run import RunRecord
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.packaging.denial import compute_denied_columns
from forge_core.profiling import build_schema_profile
from forge_core.profiling.quality import build_data_review
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
) -> RunRecord:
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
        )
    except Exception as exc:  # the orchestrator must never raise past the caller
        record.status = RunStatus.FAILED
        record.error = str(exc)
        record.log(record.current_stage or RunStage.INGEST, f"Run failed: {exc}")
    return record


def _run_pipeline_inner(
    record: RunRecord,
    *,
    packs_root: Path,
    profiling_provider: LLMProvider | None,
    generation_provider: LLMProvider | None,
    critique_provider: LLMProvider | None,
    binding_overrides: dict[str, str] | None,
    use_agent: bool = False,
) -> None:
    # True when this invocation continues a run that already paused once:
    # `data_review` is only ever set after the PROFILE stage completes, so a
    # non-None review at entry means a prior pass got this far. The stages up
    # to the pause are re-executed below (the documented "resume re-runs from
    # ingest" constraint) but NOT re-reported - the client already watched
    # them once and the review is persisted, so replaying them would make a
    # resumed run look like it started over from step 1.
    resumed = record.data_review is not None

    def log_progress(stage: RunStage, message: str, **data: Any) -> None:
        if not resumed or stage not in (RunStage.INGEST, RunStage.PROFILE, RunStage.CLASSIFY):
            record.log(stage, message, **data)

    log_progress(RunStage.INGEST, f"Ingesting {redact_connection_string(record.source_path)}")
    # Pass the raw string, not Path(record.source_path) - a live-database
    # connection string must reach registry.ingest() unmangled (Path()
    # would rewrite its "//" and turn "/" into "\" on Windows).
    data_source = ingest(record.source_path)
    log_progress(
        RunStage.INGEST,
        f"Ingested {len(data_source.tables)} table(s)",
        tables=[t.name for t in data_source.tables],
    )

    # Loaded before PROFILE (not just before CLASSIFY, where it's used) so
    # the data-understanding agent (use_agent=True) can ground an industry
    # guess in the real candidate list instead of a blind guess.
    packs = load_all_packs(packs_root)

    log_progress(RunStage.PROFILE, "Profiling schema")
    profile = build_schema_profile(data_source, profiling_provider, use_agent=use_agent, packs=packs)
    log_progress(RunStage.PROFILE, "Profile complete", columns=len(profile.structural.columns))

    # Computed once and reused on every resume - see RunRecord.data_review's
    # docstring. Wrapped in its own try/except: a data-quality review must
    # inform, never block, so it can never be what turns this whole run into
    # a FAILED one (unlike everything else in this function, which is
    # allowed to raise up into run_pipeline's own catch-all).
    if record.data_review is None:
        con = open_session(data_source)
        try:
            record.data_review = build_data_review(
                data_source,
                profile.structural,
                con,
# No provider => findings only, no LLM-phrased questions.
            # Set when the caller has already declared it won't ask (e.g. the
            # CLI's default, data_answers={}, without --review) - no point
            # paying for question generation nobody will read answers to.
            # A *non-empty* data_answers still regenerates questions: to_context
            # joins each answer back to its question's text, and that join is
            # impossible if the questions were never (re)generated.
            provider=(
                profiling_provider
                if record.data_answers is None or record.data_answers
                else None
            ),
            semantic=profile.semantic,
        )
        except Exception as exc:  # noqa: BLE001 - informs, never blocks
            record.data_review = DataReview(generated_at=datetime.now(UTC).isoformat())
            log_progress(RunStage.PROFILE, f"Data-quality review unavailable: {exc}")
        finally:
            con.close()
    log_progress(
        RunStage.PROFILE,
        f"Data quality: {len(record.data_review.findings)} finding(s)",
        review=record.data_review.model_dump(mode="json"),
    )

    # The single source of truth for every downstream consumer that needs the
    # review (binding, generation, packaging) - computed once, so all four
    # see the exact same notes/findings.
    data_context = record.data_review.to_context(record.data_answers or {})

    log_progress(RunStage.CLASSIFY, "Classifying industry")
    classification = classify(profile, packs)
    suggested_industry = profile.semantic.suggested_industry if profile.semantic else None
    log_progress(
        RunStage.CLASSIFY,
        f"Top match: {classification.primary_pack_slug} "
        f"({classification.ranked_matches[0].confidence:.2f})",
        ranked_matches=[m.model_dump() for m in classification.ranked_matches],
        requires_customer_confirmation=classification.requires_customer_confirmation,
        # The data-understanding agent's read of the data (use_agent=True
        # only) - advisory, shown next to the deterministic ranking, never
        # used to pick a pack itself. None whenever the agent didn't run.
        suggested_industry=suggested_industry.model_dump() if suggested_industry else None,
    )

    # One merged pause, never two: a run waits on the user only when there's
    # genuinely something to ask - an ambiguous industry match and/or a
    # data-quality review with questions. Both gates are pure functions of
    # the record, so no "am I resuming?" branch is needed - a clean +
    # confident dataset computes findings, gets no questions, and runs
    # straight through.
    needs_industry = classification.requires_customer_confirmation and not record.industry_override
    needs_answers = bool(record.data_review.questions) and record.data_answers is None
    if needs_industry or needs_answers:
        record.status = RunStatus.NEEDS_INPUT
        record.log(
            RunStage.CLASSIFY,
            "Awaiting customer input",
            needs_industry=needs_industry,
            needs_answers=needs_answers,
        )
        return

    pack_slug = record.industry_override or classification.primary_pack_slug
    pack = load_pack(packs_root / pack_slug)

    record.log(RunStage.BIND, f"Binding schema to {pack.slug}" + (" (agent-assisted)" if use_agent else ""))
    bindings = resolve_bindings(
        profile,
        pack,
        profiling_provider,
        binding_overrides,
        use_agent=use_agent,
        data_context=data_context,
        tenant_id=record.tenant_id,
    )
    record.log(
        RunStage.BIND,
        f"Bound {len(bindings.columns)} role(s); {len(bindings.unresolved_roles)} unresolved",
        unresolved_roles=bindings.unresolved_roles,
        agent_bound_roles=[c.role for c in bindings.columns if c.source == "agent_proposed"],
    )

    record.log(RunStage.COMPILE_KPIS, "Compiling KPIs")
    kpi_defs = compile_all(pack, bindings)

    # Optional (use_agent=True): a few extra, customer-specific KPI
    # candidates on top of the pack's own hand-authored catalog. Every
    # candidate goes through the exact same compile_kpi gate a pack KPI
    # does - a bad proposal just lands in .skipped with a reason, never a
    # new trust surface (see compiler/kpi_proposer.py's docstring).
    agent_proposed_ids: list[str] = []
    if use_agent and generation_provider is not None:
        for candidate in propose_kpis(pack, bindings, generation_provider, data_context):
            try:
                kpi_defs.kpis.append(compile_kpi(candidate, bindings, source="agent_proposed"))
                agent_proposed_ids.append(candidate.id)
            except KpiCompileError as exc:
                kpi_defs.skipped[candidate.id] = str(exc)

    record.log(
        RunStage.COMPILE_KPIS,
        f"Compiled {len(kpi_defs.kpis)}/{len(pack.kpis)} KPI(s)"
        + (f" (+{len(agent_proposed_ids)} AI-suggested)" if agent_proposed_ids else ""),
        skipped=kpi_defs.skipped,
        agent_proposed=agent_proposed_ids,
    )

    record.log(RunStage.GENERATE, "Generating plugin content")
    generated = generate_plugin_content(pack, kpi_defs, data_source, generation_provider, data_context)
    record.log(RunStage.GENERATE, "Generation complete", commands=[c.name for c in generated.commands])

    record.log(RunStage.PACKAGE, "Packaging plugin")
    denied_by_table = compute_denied_columns(profile, pack)
    spec = build_plugin_spec(
        pack,
        profile,
        bindings,
        kpi_defs,
        generated,
        customer_label=record.label,
        data_context=data_context,
        denied_by_table=denied_by_table,
    )
    plugin_dir = Path(record.output_dir) / spec.manifest.name
    write_plugin(
        spec,
        plugin_dir,
        source=data_source,
        profile=profile,
        pack=pack,
        denied_by_table=denied_by_table,
    )
    record.log(RunStage.PACKAGE, f"Packaged to {plugin_dir}", plugin_dir=str(plugin_dir))

    record.log(RunStage.VALIDATE, "Running validation harness (10 checks)")
    report = run_harness(
        pack=pack,
        profile=profile,
        bindings=bindings,
        kpi_defs=kpi_defs,
        generated=generated,
        provider=critique_provider,
        plugin_dir=plugin_dir,
        config_dir=plugin_dir / "config",
        data_dir=plugin_dir / "data",
        on_check=lambda result: record.log(
            RunStage.VALIDATE, f"{result.check}: {result.status.value}", check=result.check, status=result.status.value
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
