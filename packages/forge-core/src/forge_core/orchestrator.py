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

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import classify, load_all_packs, load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.postgres import redact as redact_connection_string
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMProvider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.profiling import build_schema_profile
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

    record.log(RunStage.PROFILE, "Profiling schema")
    profile = build_schema_profile(data_source, profiling_provider)
    record.log(RunStage.PROFILE, "Profile complete", columns=len(profile.structural.columns))

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
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)
    plugin_dir = Path(record.output_dir) / spec.manifest.name
    write_plugin(spec, plugin_dir, source=data_source, profile=profile, pack=pack)
    record.log(RunStage.PACKAGE, f"Packaged to {plugin_dir}", plugin_dir=str(plugin_dir))

    record.log(RunStage.VALIDATE, "Running validation harness")
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
