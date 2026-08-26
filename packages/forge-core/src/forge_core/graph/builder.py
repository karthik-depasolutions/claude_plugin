"""LangGraph workflow builder for Data2plugin.

Assembles the pipeline as a compiled `StateGraph[ForgeState]`.
Each node performs one focused stage transition with explicit state updates.
Human-in-the-loop gates (industry confirmation, data-quality review, and binding confirmation)
are handled seamlessly through state status inspection or native graph interrupts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from forge_core.agentic.agents.context_discovery import run_context_discovery_agent
from forge_core.binding import gate_bindings, resolve_bindings
from forge_core.classification import classify, load_all_packs, load_pack
from forge_core.compiler import compile_all
from forge_core.compiler.kpi_compiler import KpiCompileError, compile_kpi
from forge_core.compiler.kpi_proposer import propose_kpis
from forge_core.compiler.metric_generator import generate_metrics
from forge_core.compiler.metric_proposer import propose_metrics
from forge_core.generation import GeneratedPlugin, generate_plugin_content
from forge_core.graph.state import ForgeState
from forge_core.ingestion.postgres import redact as redact_connection_string
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMProvider
from forge_core.models.bindings import SchemaBindings
from forge_core.models.claims import ColumnClaim
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.data_map import DataMap
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.quality import DataReview
from forge_core.models.run import RunRecord
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.packaging.denial import compute_denied_columns
from forge_core.profiling import build_schema_profile
from forge_core.profiling.quality import build_data_review
from forge_core.runtime_session import open_session
from forge_core.validation import run_harness

logger = logging.getLogger("forge_core.graph")


def _apply_binding_confirmations(
    bindings: SchemaBindings, confirmations: dict[str, str], gated_roles: set[str]
) -> SchemaBindings:
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


class ForgeGraphContext:
    """Carries dependencies, providers, and logging hooks across graph execution."""

    def __init__(
        self,
        record: RunRecord,
        *,
        packs_root: Path,
        profiling_provider: LLMProvider | None = None,
        generation_provider: LLMProvider | None = None,
        critique_provider: LLMProvider | None = None,
        on_event: Callable[[RunStage, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.record = record
        self.packs_root = packs_root
        self.profiling_provider = profiling_provider
        self.generation_provider = generation_provider
        self.critique_provider = critique_provider
        self.on_event = on_event

    def log(self, stage: RunStage, message: str, **kwargs: Any) -> None:
        self.record.log(stage, message, **kwargs)
        if self.on_event:
            self.on_event(stage, message, kwargs)


def create_forge_graph(ctx: ForgeGraphContext) -> StateGraph:
    """Build the compiled StateGraph for the Forge pipeline."""
    graph = StateGraph(ForgeState)

    # --- Node: Ingest ---
    def node_ingest(state: ForgeState) -> dict[str, Any]:
        source_path = state["source_path"]
        ctx.log(RunStage.INGEST, f"Ingesting data source from {redact_connection_string(source_path)}")
        ds = ingest(source_path)
        tables_summary = [f"{t.name} ({t.row_count:,} rows, {len(t.columns)} cols)" for t in ds.tables]
        ctx.log(
            RunStage.INGEST,
            f"Ingested {len(ds.tables)} table(s): {', '.join(tables_summary)}",
            tables=[{"name": t.name, "rows": t.row_count, "cols": len(t.columns)} for t in ds.tables],
        )
        return {
            "current_stage": RunStage.INGEST,
            "data_source": ds,
        }

    # --- Node: Profile ---
    def node_profile(state: ForgeState) -> dict[str, Any]:
        ds = state["data_source"]
        ctx.log(RunStage.PROFILE, "Profiling schema (structural facts + LLM semantic exploration)")
        all_packs = load_all_packs(ctx.packs_root)

        def _on_agent_stats(stats: dict[str, Any]) -> None:
            ctx.record.data_understanding = stats
            ctx.log(RunStage.PROFILE, "Data understanding analysis complete", data_understanding=stats)

        profile = build_schema_profile(
            ds,
            ctx.profiling_provider,
            use_agent=state.get("use_agent", True),
            packs=all_packs,
            on_agent_stats=_on_agent_stats,
        )
        ctx.log(
            RunStage.PROFILE,
            f"Profiled {len(ds.tables)} table(s), {len(profile.structural.columns)} column(s)",
            insights_count=len(profile.semantic.candidate_insights) if profile.semantic else 0,
        )

        data_review = state.get("data_review")
        if data_review is None:
            con = open_session(ds)
            try:
                data_review = build_data_review(
                    ds,
                    profile.structural,
                    con,
                    provider=(
                        ctx.profiling_provider
                        if state.get("data_answers") is None or state.get("data_answers")
                        else None
                    ),
                    semantic=profile.semantic,
                    pack=None,
                )
            except Exception as exc:
                from datetime import UTC, datetime
                data_review = DataReview(generated_at=datetime.now(UTC).isoformat())
                ctx.log(RunStage.PROFILE, f"Data-quality review unavailable: {exc}")
            finally:
                con.close()

        # Run Context Discovery Agent
        biz_context = None
        try:
            biz_context = run_context_discovery_agent(
                ds,
                profile.structural,
                all_packs,
                on_stats=_on_agent_stats,
            )
            ctx.record.business_context = biz_context.model_dump(mode="json")
            ctx.log(
                RunStage.PROFILE,
                f"Business context discovered: grain='{biz_context.record_grain}', {len(biz_context.inferred_hypotheses)} hypotheses, {len(biz_context.open_questions)} open questions",
                business_context=biz_context.model_dump(mode="json"),
            )
        except Exception as exc:
            logger.warning("Context discovery execution failed: %s", exc)

        ctx.log(
            RunStage.PROFILE,
            f"Data review: {len(data_review.findings)} finding(s), {len(data_review.questions)} question(s)",
            review=data_review.model_dump(mode="json"),
        )
        return {
            "current_stage": RunStage.PROFILE,
            "profile": profile,
            "structural": profile.structural,
            "data_map": profile.structural.data_map,
            "data_review": data_review,
            "business_context": biz_context.model_dump(mode="json") if biz_context else None,
        }

    # --- Node: Classify ---
    def node_classify(state: ForgeState) -> dict[str, Any]:
        profile = state["profile"]
        data_review = state.get("data_review")
        industry_override = state.get("industry_override")
        data_answers = state.get("data_answers") or {}

        ctx.log(RunStage.CLASSIFY, "Classifying industry against available packs")
        classification = classify(profile, load_all_packs(ctx.packs_root))
        matches = classification.ranked_matches
        top_match = matches[0]
        ctx.log(
            RunStage.CLASSIFY,
            f"Top match: {top_match.pack_slug} ({top_match.confidence:.0%})",
            ranked_matches=[m.model_dump(mode="json") for m in matches],
            suggested_industry=profile.semantic.suggested_industry if profile.semantic else None,
        )

        needs_industry = industry_override is None and top_match.confidence < 0.45
        needs_answers = bool(data_review and data_review.questions and not data_answers)

        if needs_industry or needs_answers:
            reasons = []
            if needs_industry:
                reasons.append(f"top industry match {top_match.pack_slug!r} is below auto-accept threshold")
            if needs_answers:
                reasons.append(f"data-quality review raised {len(data_review.questions)} question(s)")
            ctx.log(
                RunStage.CLASSIFY,
                f"Awaiting customer input: {'; '.join(reasons)}",
                needs_industry=needs_industry,
                needs_answers=needs_answers,
            )
            return {
                "current_stage": RunStage.CLASSIFY,
                "status": RunStatus.NEEDS_INPUT,
                "ranked_matches": matches,
            }

        pack_slug = industry_override or top_match.pack_slug
        pack = load_pack(ctx.packs_root / pack_slug)
        ctx.log(RunStage.CLASSIFY, f"Using industry pack: {pack.slug} ({pack.name})", pack=pack.slug)
        return {
            "current_stage": RunStage.CLASSIFY,
            "status": RunStatus.RUNNING,
            "ranked_matches": matches,
            "selected_pack": pack,
        }

    # --- Node: Bind ---
    def node_bind(state: ForgeState) -> dict[str, Any]:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return {}

        pack = state["selected_pack"]
        profile = state["profile"]
        data_review = state.get("data_review")
        data_answers = state.get("data_answers") or {}
        use_agent = state.get("use_agent", True)
        binding_confirmations = state.get("binding_confirmations")
        binding_overrides = state.get("binding_overrides") or {}
        data_context = data_review.to_context(data_answers) if data_review else None

        ctx.log(RunStage.BIND, f"Resolving schema bindings for {pack.slug}")
        column_claims: dict[str, ColumnClaim] = {}

        def _capture_claims(agent_claims: dict[str, tuple[str, ColumnClaim]]) -> None:
            for _role, (physical_column, claim) in agent_claims.items():
                column_claims[f"{claim.table}.{physical_column}"] = claim

        initial_bindings = resolve_bindings(
            profile,
            pack,
            ctx.profiling_provider,
            binding_overrides,
            use_agent=use_agent,
            data_context=data_context,
            tenant_id=state.get("tenant_id", "_local"),
            on_agent_stats=lambda stats: ctx.log(
                RunStage.BIND, "Binding agent invocation", agent="binding", **stats
            ),
            on_agent_claims=_capture_claims,
        )

        kpi_defs = compile_all(pack, initial_bindings)
        binding_questions = gate_bindings(initial_bindings, pack, kpi_defs)

        if binding_questions and binding_confirmations is None:
            ctx.log(
                RunStage.BIND,
                f"Pausing for binding confirmation: {len(binding_questions)} role(s) matched below auto-accept threshold",
                questions=[q.model_dump(mode="json") for q in binding_questions],
            )
            return {
                "current_stage": RunStage.BIND,
                "status": RunStatus.NEEDS_INPUT,
                "bindings": initial_bindings,
                "binding_questions": [q.model_dump(mode="json") for q in binding_questions],
                "column_claims": list(column_claims.values()),
            }

        if binding_confirmations and binding_questions:
            gated_roles = {q.role for q in binding_questions}
            initial_bindings = _apply_binding_confirmations(initial_bindings, binding_confirmations, gated_roles)
            kpi_defs = compile_all(pack, initial_bindings)

        ctx.log(
            RunStage.BIND,
            f"Bound {len(initial_bindings.columns)} column(s), {len(initial_bindings.unresolved_roles)} unresolved",
            bindings=initial_bindings.model_dump(mode="json"),
            unresolved_roles=initial_bindings.unresolved_roles,
        )
        return {
            "current_stage": RunStage.BIND,
            "status": RunStatus.RUNNING,
            "bindings": initial_bindings,
            "kpi_defs": kpi_defs,
            "column_claims": list(column_claims.values()),
        }

    # --- Node: Compile KPIs ---
    def node_compile(state: ForgeState) -> dict[str, Any]:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return {}

        pack = state["selected_pack"]
        bindings = state["bindings"]
        data_source = state["data_source"]
        data_review = state.get("data_review")
        data_answers = state.get("data_answers") or {}
        column_claims = state.get("column_claims") or []
        use_agent = state.get("use_agent", True)

        ctx.log(RunStage.COMPILE_KPIS, "Compiling KPI queries against bound schema")
        kpi_defs = state.get("kpi_defs") or compile_all(pack, bindings)

        data_context = data_review.to_context(data_answers) if data_review else None

        agent_proposed_kpi_ids: list[str] = []
        if use_agent and ctx.generation_provider is not None:
            for candidate in propose_kpis(pack, bindings, ctx.generation_provider, data_context):
                try:
                    kpi_defs.kpis.append(compile_kpi(candidate, bindings, source="agent_proposed"))
                    agent_proposed_kpi_ids.append(candidate.id)
                except KpiCompileError as exc:
                    kpi_defs.skipped[candidate.id] = str(exc)

        ctx.log(
            RunStage.COMPILE_KPIS,
            f"Compiled {len(kpi_defs.kpis)}/{len(pack.kpis)} KPI(s)"
            + (f" (+{len(agent_proposed_kpi_ids)} AI-suggested)" if agent_proposed_kpi_ids else ""),
            skipped=kpi_defs.skipped,
            agent_proposed=agent_proposed_kpi_ids,
        )

        denied_by_table = compute_denied_columns(state["profile"], pack)
        denied_flat = {name for cols in denied_by_table.values() for name in cols}
        fact_table = bindings.table("fact").grain

        metric_defs = generate_metrics(fact_table, state["profile"].structural, denied_flat, claims=column_claims)

        agent_proposed_metric_ids: list[str] = []
        if use_agent and ctx.generation_provider is not None:
            physical_ref = {t.name: t.physical_ref for t in data_source.tables}
            proposed_metrics = propose_metrics(
                pack, bindings, metric_defs, physical_ref, ctx.generation_provider, data_context
            )
            metric_defs.extend(proposed_metrics)
            agent_proposed_metric_ids = [m.id for m in proposed_metrics]

        return {
            "current_stage": RunStage.COMPILE_KPIS,
            "kpi_defs": kpi_defs,
            "metric_defs": metric_defs,
            "agent_proposed_metrics": agent_proposed_metric_ids,
            "denied_by_table": denied_by_table,
        }

    # --- Node: Generate ---
    def node_generate(state: ForgeState) -> dict[str, Any]:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return {}

        pack = state["selected_pack"]
        kpi_defs = state["kpi_defs"]
        data_source = state["data_source"]
        data_review = state.get("data_review")
        data_answers = state.get("data_answers") or {}
        data_understanding = state.get("data_understanding")

        ctx.log(RunStage.GENERATE, "Generating plugin content")
        data_context = data_review.to_context(data_answers) if data_review else None
        generated = generate_plugin_content(
            pack, kpi_defs, data_source, ctx.generation_provider, data_context, data_understanding
        )
        ctx.log(RunStage.GENERATE, "Generation complete", commands=[c.name for c in generated.commands])
        return {
            "current_stage": RunStage.GENERATE,
            "generated_content": generated,
        }

    # --- Node: Package ---
    def node_package(state: ForgeState) -> dict[str, Any]:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return {}

        pack = state["selected_pack"]
        profile = state["profile"]
        bindings = state["bindings"]
        kpi_defs = state["kpi_defs"]
        generated = state["generated_content"]
        data_source = state["data_source"]
        data_review = state.get("data_review")
        data_answers = state.get("data_answers") or {}
        denied_by_table = state["denied_by_table"]
        metric_defs = state["metric_defs"]
        agent_proposed_metrics = state.get("agent_proposed_metrics") or []
        output_dir = state["output_dir"]
        label = state.get("label")

        ctx.log(RunStage.PACKAGE, "Packaging plugin")
        data_context = data_review.to_context(data_answers) if data_review else None
        ctx.log(
            RunStage.PACKAGE,
            f"Generated {len(metric_defs)} parameterized metric(s)"
            + (f" (+{len(agent_proposed_metrics)} AI-suggested)" if agent_proposed_metrics else ""),
            agent_proposed_metrics=agent_proposed_metrics,
        )

        spec = build_plugin_spec(
            pack,
            profile,
            bindings,
            kpi_defs,
            generated,
            customer_label=label,
            data_context=data_context,
            denied_by_table=denied_by_table,
            metric_defs=metric_defs,
            data_understanding=state.get("data_understanding"),
        )
        plugin_dir = Path(output_dir) / spec.manifest.name
        write_plugin(
            spec,
            plugin_dir,
            source=data_source,
            profile=profile,
            pack=pack,
            denied_by_table=denied_by_table,
        )
        ctx.log(RunStage.PACKAGE, f"Packaged to {plugin_dir}", plugin_dir=str(plugin_dir))
        return {
            "current_stage": RunStage.PACKAGE,
            "plugin_dir": str(plugin_dir),
        }

    # --- Node: Validate ---
    def node_validate(state: ForgeState) -> dict[str, Any]:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return {}

        pack = state["selected_pack"]
        profile = state["profile"]
        bindings = state["bindings"]
        kpi_defs = state["kpi_defs"]
        generated = state["generated_content"]
        plugin_dir = Path(state["plugin_dir"])

        ctx.log(RunStage.VALIDATE, "Running validation harness (10 checks)")
        report = run_harness(
            pack=pack,
            profile=profile,
            bindings=bindings,
            kpi_defs=kpi_defs,
            generated=generated,
            provider=ctx.critique_provider,
            plugin_dir=plugin_dir,
            config_dir=plugin_dir / "config",
            data_dir=plugin_dir / "data",
            on_check=lambda result: ctx.log(
                RunStage.VALIDATE, f"{result.check}: {result.status.value}", check=result.check, status=result.status.value
            ),
        )

        from forge_core.models.common import CheckStatus
        from forge_core.validation.diagnostic import diagnose_and_repair_validation

        if report.overall == CheckStatus.FAIL:
            report, diag = diagnose_and_repair_validation(
                report, kpi_defs, state.get("metric_defs"), bindings=bindings, pack=pack
            )
            if diag.repaired:
                ctx.log(
                    RunStage.VALIDATE,
                    f"Validation diagnostic repaired {len(diag.remedies)} issue(s)",
                    remedies=[r.model_dump() for r in diag.remedies],
                )
                # Re-package with repaired definitions
                spec = build_plugin_spec(
                    pack,
                    profile,
                    bindings,
                    kpi_defs,
                    generated,
                    customer_label=state.get("label"),
                    data_context=state.get("data_review").to_context(state.get("data_answers") or {}) if state.get("data_review") else None,
                    denied_by_table=state.get("denied_by_table") or {},
                    metric_defs=state.get("metric_defs") or [],
                    data_understanding=state.get("data_understanding"),
                )
                write_plugin(
                    spec,
                    plugin_dir,
                    source=state["data_source"],
                    profile=profile,
                    pack=pack,
                    denied_by_table=state.get("denied_by_table") or {},
                )
                # Re-run validation harness
                report = run_harness(
                    pack=pack,
                    profile=profile,
                    bindings=bindings,
                    kpi_defs=kpi_defs,
                    generated=generated,
                    provider=ctx.critique_provider,
                    plugin_dir=plugin_dir,
                    config_dir=plugin_dir / "config",
                    data_dir=plugin_dir / "data",
                )

        ctx.log(
            RunStage.VALIDATE,
            f"Validation overall: {report.overall.value}",
            overall=report.overall.value,
            report=report.model_dump(mode="json"),
        )

        status = RunStatus.FAILED if report.overall == CheckStatus.FAIL else RunStatus.SUCCEEDED
        error = "Validation harness reported hard failures; see the validate stage event." if status == RunStatus.FAILED else None
        return {
            "current_stage": RunStage.VALIDATE,
            "status": status,
            "error": error,
            "validation_report": report,
        }

    # --- Wire Graph ---
    graph.add_node("ingest", node_ingest)
    graph.add_node("profile", node_profile)
    graph.add_node("classify", node_classify)
    graph.add_node("bind", node_bind)
    graph.add_node("compile", node_compile)
    graph.add_node("generate", node_generate)
    graph.add_node("package", node_package)
    graph.add_node("validate", node_validate)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "profile")
    graph.add_edge("profile", "classify")

    # Conditional branching after classify
    def _route_after_classify(state: ForgeState) -> str:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return END
        return "bind"

    graph.add_conditional_edges("classify", _route_after_classify, ["bind", END])

    # Conditional branching after bind
    def _route_after_bind(state: ForgeState) -> str:
        if state.get("status") == RunStatus.NEEDS_INPUT:
            return END
        return "compile"

    graph.add_conditional_edges("bind", _route_after_bind, ["compile", END])

    graph.add_edge("compile", "generate")
    graph.add_edge("generate", "package")
    graph.add_edge("package", "validate")
    graph.add_edge("validate", END)

    return graph


__all__ = ["ForgeGraphContext", "create_forge_graph"]
