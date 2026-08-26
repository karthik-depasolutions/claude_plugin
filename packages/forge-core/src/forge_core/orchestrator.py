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

import os
from datetime import UTC, datetime
from pathlib import Path

from forge_core.agentic.agents.context_discovery import (
    answers_to_business_answers,
    merge_context_questions_into_review,
    run_context_discovery_agent,
)
from forge_core.agentic.schemas.business_context import BusinessContext
from forge_core.binding import gate_bindings, resolve_bindings
from forge_core.classification import classify, load_all_packs, load_pack
from forge_core.compiler import compile_all
from forge_core.compiler.kpi_compiler import KpiCompileError, compile_kpi
from forge_core.compiler.kpi_proposer import propose_kpis
from forge_core.compiler.metric_generator import generate_metrics
from forge_core.compiler.metric_proposer import propose_metrics
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.postgres import redact as redact_connection_string
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMProvider
from forge_core.models.bindings import SchemaBindings
from forge_core.models.claims import ColumnClaim
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.quality import DataReview
from forge_core.models.run import RunRecord
from forge_core.models.schema_profile import SemanticProfile
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.packaging.denial import compute_denied_columns
from forge_core.profiling import build_schema_profile
from forge_core.profiling.quality import build_data_review
from forge_core.runtime_session import open_session
from forge_core.validation import run_harness

# U1 — DataUnderstanding artifact (deterministic, never blocks pipeline)
try:
    from forge_core.models.data_understanding import DomainAssessment  # noqa: F401
    from forge_core.understanding.builder import build_data_understanding  # noqa: F401
except Exception:  # pragma: no cover - import guard for partial installs
    build_data_understanding = None  # type: ignore[assignment]
    DomainAssessment = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PACKS_ROOT = REPO_ROOT / "industry-packs"


def _apply_binding_confirmations(
    bindings: SchemaBindings, confirmations: dict[str, str], gated_roles: set[str]
) -> SchemaBindings:
    """Applies P1-08's binding-gate answers. Only touches roles that were
    actually gated this run (recomputed fresh from gate_bindings, same as
    what the pause showed) - every other binding passes through untouched.
    A gated role answered with its proposed column or one of its listed
    alternatives becomes a human_override at full confidence. Anything
    else - an explicit decline, or simply no answer for that role - is
    treated as declined: the binding is dropped and the role becomes
    unresolved, so dependent KPIs land in .skipped with a clear reason
    instead of shipping the unconfirmed guess. Silence is not consent for
    a binding this risky."""
    new_columns = []
    new_unresolved = list(bindings.unresolved_roles)
    for binding in bindings.columns:
        if binding.role not in gated_roles:
            new_columns.append(binding)
            continue
        answer = confirmations.get(binding.role, "")
        valid_choices = {binding.physical, *(name for name, _ in binding.alternatives)}
        if answer and answer in valid_choices:
            new_columns.append(
                binding.model_copy(
                    update={
                        "physical": answer,
                        "confidence": 1.0,
                        "evidence": "human-confirmed via binding gate",
                        "source": "human_override",
                        "needs_confirmation": False,
                        "alternatives": [],
                    }
                )
            )
        else:
            new_unresolved.append(binding.role)
    return bindings.model_copy(update={"columns": new_columns, "unresolved_roles": new_unresolved})


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

    # --- LLM cost accounting -------------------------------------------
    # Two kinds of call sites spend tokens and neither reported them before:
    # the LLMProvider path (profiling/generation/critique) now accumulates
    # into a UsageTracker we drain here, and the LangChain agents report
    # through AgentCallRecorder's `on_stats` callbacks. Both funnel into
    # record.token_usage so "what did this plugin cost to build" has one
    # answer instead of being scattered across event payloads.
    _providers = {
        "profiling": profiling_provider,
        "generation": generation_provider,
        "critique": critique_provider,
    }

    def bill_providers() -> None:
        """Attribute whatever each provider has accrued since the last call."""
        for component, provider in _providers.items():
            drain = getattr(provider, "drain_usage", None)
            if drain is None:
                continue
            usage = drain()
            if usage.get("llm_calls"):
                record.token_usage.add(component, usage)

    def bill_agent(component: str, stats: dict[str, Any]) -> None:
        record.token_usage.add(component, stats)

    def usage_payload() -> dict[str, Any]:
        """Cost-so-far, as extra `data` on whatever event is already being
        logged at an exit point - a pause or the final validation result.

        Deliberately NOT its own StageEvent: several consumers locate a
        stage's payload by taking the *last* event of that stage (the API's
        download/publish lookups, the e2e tests' `plugin_dir`, the pause
        flags), so an extra trailing event per stage silently shadows the one
        they meant. Attaching to the existing event keeps the number visible
        at every exit - including a run parked on a human for hours, which
        has usually already paid for the expensive agent passes - without
        adding a shadowing event anywhere."""
        bill_providers()
        usage = record.token_usage
        return {"token_usage": usage.model_dump(mode="json"), "total_tokens": usage.total_tokens}

    # use_agent is now the default for every real run (CLI/API), but every
    # LangChain agent construct-and-invoke is wrapped in a bare
    # `except Exception` (agentic/*.py, understanding/agent.py) so it can
    # never crash the pipeline - which means a missing API key degrades
    # silently to zero agent-produced claims, indistinguishable from "the
    # agent looked and found nothing confident to say". That silence was an
    # acceptable trade-off when the agent was opt-in; it isn't once it's the
    # expected path, so it's surfaced here, loudly, once, up front.
    if use_agent and not os.environ.get("GEMINI_API_KEY"):
        log_progress(
            RunStage.PROFILE,
            "GEMINI_API_KEY is not set - the agentic understanding pass cannot run this time. "
            "Falling back to deterministic-only resolution; more roles than usual may need "
            "manual confirmation.",
        )

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
    profile = build_schema_profile(
        data_source,
        profiling_provider,
        use_agent=use_agent,
        packs=packs,
        # Agent LLM spend is otherwise invisible to record.events (the two
        # agents call ChatGoogleGenerativeAI directly, not through the
        # cassette-wrapped provider) - surface one StageEvent per invocation.
        on_agent_stats=lambda stats: (
            bill_agent("data_understanding", stats),
            record.log(
                RunStage.PROFILE,
                "Data-understanding agent",
                agent="data_understanding",
                **stats,
            ),
        )[-1],
        on_progress=lambda msg: log_progress(RunStage.PROFILE, msg),
        # Reuse the semantic pass this run already paid for. Structural
        # profiling still re-runs (deterministic and cheap); this is only
        # about not re-invoking the most expensive agent in the pipeline on
        # every pause/resume cycle.
        cached_semantic=(
            SemanticProfile.model_validate(record.semantic_profile)
            if record.semantic_profile
            else None
        ),
    )
    if profile.semantic is not None and record.semantic_profile is None:
        record.semantic_profile = profile.semantic.model_dump(mode="json")
    bill_providers()
    log_progress(RunStage.PROFILE, "Profile complete", columns=len(profile.structural.columns))

    candidate_pack = None
    if record.industry_override:
        try:
            candidate_pack = load_pack(packs_root / record.industry_override)
        except Exception:
            candidate_pack = None
    elif profile.semantic and profile.semantic.suggested_industry and profile.semantic.suggested_industry.pack_slug_guess:
        try:
            candidate_pack = load_pack(packs_root / profile.semantic.suggested_industry.pack_slug_guess)
        except Exception:
            candidate_pack = None

    # Computed once and reused on every resume - see RunRecord.data_review's
    # docstring. Wrapped in its own try/except: a data-quality review must
    # inform, never block, so it can never be what turns this whole run into
    # a FAILED one (unlike everything else in this function, which is
    # allowed to raise up into run_pipeline's own catch-all).
    if record.data_review is None:
        con = open_session(data_source)
        try:
            review_kwargs: dict[str, Any] = {}
            if candidate_pack is not None:
                review_kwargs["pack"] = candidate_pack
            record.data_review = build_data_review(
                data_source,
                profile.structural,
                con,
                provider=(
                    profiling_provider
                    if record.data_answers is None or record.data_answers
                    else None
                ),
                semantic=profile.semantic,
                **review_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - informs, never blocks
            record.data_review = DataReview(generated_at=datetime.now(UTC).isoformat())
            log_progress(RunStage.PROFILE, f"Data-quality review unavailable: {exc}")
        finally:
            con.close()
    # Run Context Discovery Agent
    if record.business_context is None:
        # Same signal compute_denied_columns uses (it derives denial purely
        # from is_likely_pii and ignores the pack), computed here because the
        # pack isn't chosen until CLASSIFY, which runs after this. Keeps the
        # agent's tools from reading a column the packaged plugin will delete.
        denied_for_agent = {c.name for c in profile.structural.columns if c.is_likely_pii}
        try:
            biz_context = run_context_discovery_agent(
                data_source,
                profile.structural,
                packs,
                # Spec §13's adaptive loop: on a resumed run the customer's
                # replies are fed back in, so discovery continues from what
                # they told us instead of re-asking what it already knows.
                answers=answers_to_business_answers(record.data_answers or {}),
                denied_columns=denied_for_agent,
                on_stats=lambda stats: (
                    bill_agent("context_discovery", stats),
                    record.log(
                        RunStage.PROFILE, "Context discovery agent", agent="context_discovery", **stats
                    ),
                )[-1],
            )
            record.business_context = biz_context.model_dump(mode="json")
            # Put the agent's open questions on the same review page as the
            # deterministic ones. Without this the agent investigates, finds
            # real ambiguities, and nobody is ever asked about them.
            #
            # Only when the agentic path is actually live. Offline, discovery
            # degrades to a structural pass whose questions substantially
            # overlap the ones `generate_business_context_questions` already
            # produced - and adding questions is what makes a run *pause*, so
            # doing it unconditionally would turn every --no-llm/CI run into
            # one that stops for human input it can't usefully act on.
            asked = 0
            if use_agent and profiling_provider is not None:
                asked = merge_context_questions_into_review(record.data_review, biz_context)
            log_progress(
                RunStage.PROFILE,
                f"Business context discovered: grain='{biz_context.record_grain}', "
                f"{len(biz_context.inferred_hypotheses)} hypotheses, "
                f"{len(biz_context.open_questions)} open question(s), {asked} to ask",
                business_context=record.business_context,
            )
        except Exception as exc:  # noqa: BLE001 - informs, never blocks
            log_progress(RunStage.PROFILE, f"Context discovery unavailable: {exc}")

    log_progress(
        RunStage.PROFILE,
        f"Data quality: {len(record.data_review.findings)} finding(s)",
        review=record.data_review.model_dump(mode="json"),
    )

    # The single source of truth for every downstream consumer that needs the
    # review (binding, generation, packaging) - computed once, so all four
    # see the exact same notes/findings.
    data_context = record.data_review.to_context(record.data_answers or {})
    # Spec §22's handoff contract. Merged into the shared payload rather than
    # threaded through four signatures, so binding, KPI proposal, generation
    # and packaging all consume the agent's findings instead of each
    # re-deriving business context for itself.
    business_handoff: dict[str, Any] = {}
    if record.business_context:
        try:
            business_handoff = BusinessContext.model_validate(record.business_context).to_handoff()
            data_context["business_context"] = business_handoff
        except Exception as exc:  # noqa: BLE001 - a bad artifact must not fail the run
            log_progress(RunStage.PROFILE, f"Business context handoff skipped: {exc}")

    log_progress(RunStage.CLASSIFY, "Classifying industry")
    classification = classify(profile, packs, business_context=business_handoff)
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

    # U1 — build DataUnderstanding (deterministic, never blocks pipeline)
    # Skipped once already computed, for the same reason as `semantic_profile`
    # and `data_review`: a resume replays from ingest, and the enrichment pass
    # inside this block makes an LLM call. Re-deriving a near-identical answer
    # at full price on every pause is pure waste.
    if build_data_understanding is not None and record.data_understanding is None:
        try:
            # DomainAssessment from classification (deterministic matcher is source of truth)
            top = classification.ranked_matches[0] if classification.ranked_matches else None
            pack_for_domain = None
            try:
                pack_for_domain = load_pack(packs_root / classification.primary_pack_slug) if top else None
            except Exception:
                pack_for_domain = None
            # Collect expected roles for domain assessment
            expected_roles: list[str] = []
            if pack_for_domain is not None:
                try:
                    expected_roles = [k.canonical_role for k in pack_for_domain.kpis]  # type: ignore[attr-defined]
                except Exception:
                    expected_roles = []
                # Fallback: try role_hints keys
                if not expected_roles and hasattr(pack_for_domain, "role_hints"):
                    expected_roles = list(getattr(pack_for_domain, "role_hints", {}).keys())
            domain_obj = None
            if DomainAssessment is not None and top is not None:
                domain_obj = DomainAssessment(
                    pack_slug=top.slug if hasattr(top, "slug") else classification.primary_pack_slug,
                    confidence=float(top.confidence) if hasattr(top, "confidence") else 0.0,
                    matched_roles=[],
                    unmatched_roles=expected_roles,
                    evidence=[f"Top match {classification.primary_pack_slug} ({getattr(top, 'confidence', 0):.2f})"],
                )
            understanding = build_data_understanding(
                profile,
                data_source,
                data_review=record.data_review,
                domain=domain_obj,
                model_name=getattr(profiling_provider, "model", None) if profiling_provider else None,
            )
            # U3 — optional agentic enrichment for ambiguous columns (use_agent=True)
            if use_agent and profiling_provider is not None and understanding.open_questions:
                try:
                    from forge_core.understanding.agent import enrich_data_understanding

                    def _on_enrich_stats(stats: dict) -> None:
                        # Bill it. This agent logged its usage but was never
                        # added to the run's totals, so its spend - measured
                        # at 126,089 input tokens on a 17-column table, more
                        # than every other component combined - was invisible
                        # in the figure shown to the user and stored in the
                        # database. A cost report that silently omits the
                        # largest line item is worse than none.
                        bill_agent("understanding", stats)
                        record.log(RunStage.PROFILE, "Understanding enrichment agent", agent="understanding", **stats)

                    enriched = enrich_data_understanding(
                        understanding,
                        profile.structural,
                        data_source,
                        model_name=getattr(profiling_provider, "model", None) if profiling_provider else None,
                        on_stats=_on_enrich_stats,
                    )
                    # Only count as enriched if it actually reduced open questions or added business questions
                    if len(enriched.open_questions) < len(understanding.open_questions) or len(
                        enriched.business_questions
                    ) > len(understanding.business_questions):
                        record.log(
                            RunStage.PROFILE,
                            f"Enriched: {len(understanding.open_questions) - len(enriched.open_questions)} columns resolved, +{len(enriched.business_questions) - len(understanding.business_questions)} questions",
                            enriched_open=len(enriched.open_questions),
                        )
                    understanding = enriched
                except Exception as exc:  # noqa: BLE001 - enrichment never blocks
                    record.log(RunStage.PROFILE, f"Enrichment skipped: {exc}")

            record.data_understanding = understanding.model_dump(mode="json")
            record.log(
                RunStage.PROFILE,
                f"DataUnderstanding: {len(understanding.columns)} columns, {len(understanding.open_questions)} open questions",
                fingerprint=understanding.source_fingerprint,
                open_questions=len(understanding.open_questions),
            )
        except Exception as exc:  # noqa: BLE001 - understanding must never block pipeline
            record.log(RunStage.PROFILE, f"DataUnderstanding unavailable: {exc}")

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
            **usage_payload(),
        )
        return

    pack_slug = record.industry_override or classification.primary_pack_slug
    pack = load_pack(packs_root / pack_slug)

    record.log(RunStage.BIND, f"Binding schema to {pack.slug}" + (" (agent-assisted)" if use_agent else ""))
    # Captured here, keyed "table.column" for generate_metrics (Part 1/2 of
    # the understanding-agent architecture) - the single wire from a
    # gate-verified semantic claim to what SUM eligibility, unit, and
    # provenance a shipped metric actually gets. Every claimed physical
    # column lives on the fact table by construction (canonical-role
    # binding is fact-table-scoped, see resolve_bindings' own docstring).
    column_claims: dict[str, ColumnClaim] = {}

    def _capture_claims(agent_claims: dict[str, tuple[str, ColumnClaim]]) -> None:
        for _role, (physical_column, claim) in agent_claims.items():
            column_claims[f"{claim.table}.{physical_column}"] = claim

    bindings = resolve_bindings(
        profile,
        pack,
        profiling_provider,
        binding_overrides,
        use_agent=use_agent,
        data_context=data_context,
        tenant_id=record.tenant_id,
        on_agent_stats=lambda stats: (
            bill_agent("binding", stats),
            record.log(
                RunStage.BIND,
                "Binding agent invocation",
                agent="binding",
                **stats,
            ),
        )[-1],
        on_agent_claims=_capture_claims,
    )
    bill_providers()
    record.log(
        RunStage.BIND,
        f"Bound {len(bindings.columns)} role(s); {len(bindings.unresolved_roles)} unresolved",
        unresolved_roles=bindings.unresolved_roles,
        agent_bound_roles=[c.role for c in bindings.columns if c.source == "agent_proposed"],
    )

    kpi_defs = compile_all(pack, bindings)
    binding_questions = gate_bindings(bindings, pack, kpi_defs)
    if binding_questions:
        if record.binding_confirmations is None:
            record.status = RunStatus.NEEDS_INPUT
            record.binding_questions = binding_questions
            record.log(
                RunStage.BIND,
                "Awaiting binding confirmation",
                questions=[q.model_dump() for q in binding_questions],
                **usage_payload(),
            )
            return
        gated_roles = {q.role for q in binding_questions}
        bindings = _apply_binding_confirmations(bindings, record.binding_confirmations, gated_roles)
        kpi_defs = compile_all(pack, bindings)  # recompile: confirmations may unlock or drop KPIs

    record.log(RunStage.COMPILE_KPIS, "Compiling KPIs")

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

    bill_providers()
    record.log(
        RunStage.COMPILE_KPIS,
        f"Compiled {len(kpi_defs.kpis)}/{len(pack.kpis)} KPI(s)"
        + (f" (+{len(agent_proposed_ids)} AI-suggested)" if agent_proposed_ids else ""),
        skipped=kpi_defs.skipped,
        agent_proposed=agent_proposed_ids,
    )

    record.log(RunStage.GENERATE, "Generating plugin content")
    generated = generate_plugin_content(
        pack, kpi_defs, data_source, generation_provider, data_context, record.data_understanding
    )
    bill_providers()
    record.log(RunStage.GENERATE, "Generation complete", commands=[c.name for c in generated.commands])

    record.log(RunStage.PACKAGE, "Packaging plugin")
    denied_by_table = compute_denied_columns(profile, pack)
    # P2-07: parameterized metrics, generated deterministically from the
    # same fact table binding resolved the frozen KPIs against - a distinct
    # capability layer, not a replacement for kpi_defs.json.
    denied_flat = {name for cols in denied_by_table.values() for name in cols}
    fact_table = bindings.table("fact").grain
    # P2-09: provenance (including the status-ambiguity caveat on currency
    # totals) is computed inside generate_metrics itself now - it needs
    # per-metric dimension context that's naturally available there, and
    # applies uniformly to fact-table and broadcast measures alike (see
    # metric_generator._provenance_for's docstring).
    metric_defs = generate_metrics(fact_table, profile.structural, denied_flat, claims=column_claims)
    # P2-08: a few extra, business-framed views over the SAME verified
    # metric catalog above - never a new measure/join/filter-value, only a
    # curated (base_metric, value-set filter) pairing (see
    # compiler/metric_proposer.py's docstring for why that's safe by
    # construction rather than by validation after the fact).
    agent_proposed_metric_ids: list[str] = []
    if use_agent and generation_provider is not None:
        physical_ref = {t.name: t.physical_ref for t in data_source.tables}
        proposed_metrics = propose_metrics(
            pack, bindings, metric_defs, physical_ref, generation_provider, data_context
        )
        metric_defs.extend(proposed_metrics)
        agent_proposed_metric_ids = [m.id for m in proposed_metrics]
    record.log(
        RunStage.PACKAGE,
        f"Generated {len(metric_defs)} parameterized metric(s)"
        + (f" (+{len(agent_proposed_metric_ids)} AI-suggested)" if agent_proposed_metric_ids else ""),
        agent_proposed_metrics=agent_proposed_metric_ids,
    )
    spec = build_plugin_spec(
        pack,
        profile,
        bindings,
        kpi_defs,
        generated,
        customer_label=record.label,
        data_context=data_context,
        denied_by_table=denied_by_table,
        metric_defs=metric_defs,
        data_understanding=record.data_understanding,
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
    # usage_payload() bills the critique pass that just ran, so this carries
    # the run's final total - the number shown as "what this plugin cost".
    record.log(
        RunStage.VALIDATE,
        f"Validation overall: {report.overall.value}",
        overall=report.overall.value,
        report=report.model_dump(mode="json"),
        **usage_payload(),
    )

    if report.overall == "fail":
        record.status = RunStatus.FAILED
        record.error = "Validation harness reported hard failures; see the validate stage event."
    else:
        record.status = RunStatus.SUCCEEDED


__all__ = ["DEFAULT_PACKS_ROOT", "run_pipeline"]
