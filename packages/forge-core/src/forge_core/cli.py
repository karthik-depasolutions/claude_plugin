"""`forge` - the CLI entry point for running the generator pipeline
in-process, without the API/web UI. Wraps `forge_core.orchestrator` with a
live-updating checklist (via `RunRecord.on_event`) so progress is visible in
real time, then prints the full chronological event log once it finishes.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from forge_core.classification import load_all_packs
from forge_core.ingestion.registry import default_run_id, prepare_source_for_persistence
from forge_core.llm import TokenUsage, get_provider
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline
from forge_core.validation.plugin_spec import check_plugin_spec

# Mirrors the order `run_pipeline` actually logs stages in (package before
# validate) - see forge_core.orchestrator and apps/web/src/lib/types.ts's
# STAGE_ORDER, which the web wizard's checklist keeps in sync with this.
_STAGE_LABELS: dict[RunStage, str] = {
    RunStage.INGEST: "Ingest data",
    RunStage.PROFILE: "Profile schema",
    RunStage.CLASSIFY: "Classify industry",
    RunStage.BIND: "Bind schema",
    RunStage.COMPILE_KPIS: "Compile KPIs",
    RunStage.GENERATE: "Generate plugin content",
    RunStage.PACKAGE: "Package plugin",
    RunStage.VALIDATE: "Run validation harness",
}
_STAGE_ORDER = list(_STAGE_LABELS)


def _render_checklist(record: RunRecord) -> Table:
    """A live-updating todo list: done stages get a checkmark, the stage
    currently running gets a spinner-ish marker plus its latest message,
    everything after it is explicitly "pending" rather than left blank."""
    latest_message: dict[RunStage, str] = {}
    for event in record.events:
        latest_message[event.stage] = event.message

    reached = _STAGE_ORDER.index(record.current_stage) if record.current_stage in _STAGE_ORDER else -1
    table = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
    for index, stage in enumerate(_STAGE_ORDER):
        is_done = index < reached or (index == reached and record.status == RunStatus.SUCCEEDED)
        is_current = index == reached and not is_done
        if is_done:
            marker, style = "[x]", "bold green"
        elif is_current and record.status == RunStatus.FAILED:
            marker, style = "[!]", "bold red"
        elif is_current:
            marker, style = "[~]", "bold cyan"
        else:
            marker, style = "[ ]", "dim"
        message = latest_message.get(stage, "" if is_done or is_current else "pending")
        table.add_row(
            Text(f"{marker} {_STAGE_LABELS[stage]}", style=style),
            Text(message, style=style),
        )
    return table


app = typer.Typer(help="MIS Plugin Forge - generate a Claude Code plugin from MIS data.")
packs_app = typer.Typer(help="Inspect and validate industry packs.")
publish_app = typer.Typer(help="Publish an already-packaged plugin (see `forge run`'s package stage output).")
app.add_typer(packs_app, name="packs")
app.add_typer(publish_app, name="publish")
console = Console()


@app.callback()
def _load_env() -> None:
    """Every subcommand may read a `FORGE_*`/`GEMINI_API_KEY`/`GITHUB_*` env
    var straight from `os.environ` (see `publish github`, `publish
    marketplace --push`) - load `.env` once, here, instead of relying on
    each of those call sites (or forge_core.llm, which already does this
    for its own use) to remember to."""
    from dotenv import load_dotenv

    load_dotenv()


@app.command()
def run(  # noqa: PLR0917 - a CLI command's options are all keyword flags, not a positional API
    source: str = typer.Argument(
        ...,
        help="Path to a data file, directory, or SQLite database - or a postgresql:// connection string.",
    ),
    output_dir: Path = typer.Option(Path("generated"), "--out", help="Where to write the plugin."),
    pack: str | None = typer.Option(
        None, "--pack", help="Industry pack slug to force (skips auto-classification)."
    ),
    use_agent: bool = typer.Option(
        False,
        "--agent/--no-agent",
        help="Use a tool-using LangChain agent (schema inspection, live data preview, web "
        "search) for schema roles the deterministic scorer and single-shot LLM proposer "
        "both fail to resolve, instead of leaving them unresolved.",
    ),
    agent_schema: bool = typer.Option(
        False,
        "--agent-schema/--no-agent-schema",
        help="Enable Agent Schema mode for large databases (50-500 tables). "
        "Reads information_schema to skip O(n\u00b2) relationship probing, caps profiling budgets, "
        "and chunks LLM synthesis so large warehouses finish in reasonable time. "
        "Output is identical to --no-agent-schema when the flag is off.",
    ),
    label: str | None = typer.Option(
        None,
        "--label",
        help="Optional project/business name (e.g. 'Sparda Music Academy') - personalizes the "
        "plugin's displayName and on-disk/repo name instead of the generic '<pack>-mis-plugin'.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Use the deterministic in-process FakeLLMProvider for every LLM role instead of a "
        "real backend. No API key needed. For CI genericity gates and offline smoke tests - the "
        "generated plugin ships template prose and no verified cookbook. Implies --fast.",
    ),
    fast: bool = typer.Option(
        False,
        "--fast/--no-fast",
        help="With --agent-schema: the minimum deterministic profiling - declared PK/FK only, "
        "no O(n²) probing, no pattern mining, no quality analysis. A 70-table remote database "
        "profiles in ~2 minutes. For exercising the pipeline quickly, not a production pack.",
    ),
) -> None:
    """Run the full pipeline end-to-end against SOURCE."""
    # SOURCE is a plain `str`, not `Path`, so Typer never mangles a
    # connection string's `//`/`/` before we get a chance to look at it.
    # prepare_source_for_persistence() tells a live-database connection
    # string apart from a filesystem path and, for the former, stashes the
    # credential in this process's env instead of ever writing it to disk.
    run_id = default_run_id(source)
    source_for_run = prepare_source_for_persistence(source)
    record = RunRecord(
        run_id=run_id,
        source_path=source_for_run,
        output_dir=str(output_dir.resolve()),
        industry_override=pack,
        label=label,
    )

    from forge_core.llm import LLMError, LLMProvider

    usage = TokenUsage()
    profiling_provider: LLMProvider | None
    generation_provider: LLMProvider | None
    critique_provider: LLMProvider | None
    if no_llm:
        from forge_core.testing import FakeLLMProvider

        profiling_provider = generation_provider = critique_provider = FakeLLMProvider()
        # --no-llm is a non-interactive mode (CI gates, smoke tests). The CLI
        # has no way to answer the pre-synthesis clarification pause, so take
        # the explicit "nothing to add" path, exactly like the e2e helpers.
        record.data_answers = {}
    else:
        try:
            profiling_provider = get_provider(role="profiling", usage=usage)
            generation_provider = get_provider(role="generation", usage=usage)
            critique_provider = get_provider(role="critique", usage=usage)
        except LLMError:
            if os.environ.get("FORGE_LLM_DEGRADED_OK", "").lower() not in ("1", "true", "yes"):
                raise
            profiling_provider = generation_provider = critique_provider = None
            record.data_answers = {}  # a degraded run cannot use clarification answers

    with Live(_render_checklist(record), console=console, refresh_per_second=8) as live:
        record.on_event(lambda _event: live.update(_render_checklist(record)))
        result = run_pipeline(
            record,
            packs_root=DEFAULT_PACKS_ROOT,
            profiling_provider=profiling_provider,
            generation_provider=generation_provider,
            critique_provider=critique_provider,
            use_agent=use_agent,
            agent_schema=agent_schema,
            fast=fast or no_llm,
        )
        # The final `record.status` transition (SUCCEEDED/FAILED) happens
        # after the last `log()` call, so the checklist needs one more
        # render to show the true end state instead of the mid-run one.
        live.update(_render_checklist(record))

    console.print()
    console.print("[bold]Detailed log:[/]")
    for event in result.events:
        stamp = event.timestamp.strftime("%H:%M:%S")
        console.print(f"  [dim]{stamp}[/] [cyan]{event.stage.value:>14}[/] {event.message}")

    s = usage.snapshot()
    if s["calls"]:
        console.print(
            f"\n[bold]LLM tokens:[/] {s['input_tokens']:,} in / {s['output_tokens']:,} out / "
            f"{s['total_tokens']:,} total across {s['calls']} calls"
        )

    if result.status == RunStatus.NEEDS_INPUT:
        console.print(
            "[yellow]Needs input:[/] industry classification was ambiguous. "
            "Re-run with --pack <slug> once you've picked one from the classify event above."
        )
        raise typer.Exit(code=2)
    if result.status == RunStatus.FAILED:
        console.print(f"[bold red]Run failed:[/] {result.error}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]Done.[/] Plugin written under {output_dir.resolve()}")


@app.command()
def validate(plugin_dir: Path = typer.Argument(..., help="Path to a packaged plugin directory.")) -> None:
    """Run just the plugin_spec structural check against an already-packaged plugin."""
    result = check_plugin_spec(plugin_dir)
    console.print(f"plugin_spec: [bold]{result.status.value}[/]")
    for issue in result.issues:
        console.print(f"  [{issue.severity}] {issue.location}: {issue.message}")
    if result.status.value == "fail":
        raise typer.Exit(code=1)


@packs_app.command("validate")
def packs_validate(
    packs_root: Path = typer.Argument(DEFAULT_PACKS_ROOT, help="Directory with one subdirectory per pack."),
) -> None:
    """Load every pack under PACKS_ROOT, which validates it against the
    IndustryPack/CanonicalKpi pydantic models (the same validation every
    pipeline run relies on) - catches authoring mistakes without a full run."""
    try:
        packs = load_all_packs(packs_root)
    except Exception as exc:  # pydantic ValidationError, FileNotFoundError, json errors, ...
        console.print(f"[bold red]Pack validation failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    for pack in packs:
        console.print(f"[green]OK[/]  {pack.slug} v{pack.version} - {len(pack.kpis)} KPI(s)")
    console.print(f"[bold green]{len(packs)} pack(s) valid.[/]")


@packs_app.command("new")
def packs_new(
    slug: str = typer.Argument(..., help="Kebab-case pack slug, e.g. 'logistics-ops'."),
    packs_root: Path = typer.Option(DEFAULT_PACKS_ROOT, "--root", help="industry-packs/ directory."),
) -> None:
    """Scaffold a new pack: pack.json + one example KPI + empty skills/agents/recipes dirs."""
    from forge_core.packs import scaffold_pack

    try:
        pack_dir = scaffold_pack(slug, packs_root)
    except (ValueError, FileExistsError) as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    console.print(f"[bold green]Created[/] {pack_dir}")
    console.print("Next: fill in canonical_roles / signature in pack.json, add KPIs under kpis/, then")
    console.print(f"  forge packs lint {slug}   and   forge packs test {slug} <dataset>")


@packs_app.command("lint")
def packs_lint(
    slug: str = typer.Argument(..., help="Pack slug (a subdir of --root)."),
    packs_root: Path = typer.Option(DEFAULT_PACKS_ROOT, "--root", help="industry-packs/ directory."),
) -> None:
    """Semantic checks beyond the JSON schema: role references resolve, every
    KPI template token is a real role/alias/value-set, templates exist,
    assertions reference real result columns."""
    from forge_core.packs import lint_pack

    try:
        findings = lint_pack(packs_root / slug)
    except Exception as exc:  # noqa: BLE001 - pydantic/json/FileNotFound: report and exit
        console.print(f"[bold red]Could not load pack:[/] {exc}")
        raise typer.Exit(code=1) from exc

    errors = [f for f in findings if f.level == "error"]
    for f in findings:
        colour = "red" if f.level == "error" else "yellow"
        console.print(f"  [{colour}]{f.level:5}[/] [{f.where}] {f.message}")
    if errors:
        console.print(f"[bold red]{len(errors)} error(s), {len(findings) - len(errors)} warning(s).[/]")
        raise typer.Exit(code=1)
    console.print(f"[bold green]lint clean[/] ({len(findings)} warning(s)).")


@packs_app.command("test")
def packs_test(
    slug: str = typer.Argument(..., help="Pack slug (a subdir of --root)."),
    dataset: Path = typer.Argument(..., help="A file / directory / SQLite DB to bind the pack against."),
    packs_root: Path = typer.Option(DEFAULT_PACKS_ROOT, "--root", help="industry-packs/ directory."),
) -> None:
    """Bind + compile + dry-run this pack against DATASET, no LLM. Reports the
    match score, per-role bind + confidence, and every KPI's compile/dry-run
    result - the fast iteration loop for pack authoring."""
    from forge_core.packs import run_pack_test

    try:
        report = run_pack_test(packs_root / slug, dataset)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    console.print(f"[bold]{report.pack_slug}[/] vs [bold]{dataset.name}[/]")
    console.print(f"  match confidence : {report.match_confidence:.2f}")
    console.print(
        f"  bind rate        : {report.bind_rate:.0%} "
        f"({report.bound_roles}/{report.total_roles} roles)"
    )
    for rb in report.role_binds:
        if rb.bound_to:
            console.print(
                f"    [green]{rb.role}[/] -> {rb.table_alias}.{rb.bound_to} "
                f"(conf {rb.confidence:.2f}, {rb.source})"
            )
        else:
            console.print(f"    [red]{rb.role}[/] -> unresolved")
    console.print(f"  KPIs compiled    : {len(report.compiled_kpis)}  {report.compiled_kpis}")
    if report.skipped_kpis:
        console.print(f"  KPIs skipped     : {report.skipped_kpis}")
    for kpi_id, outcome in report.dry_run:
        colour = "red" if outcome.startswith("ERROR") else "green"
        console.print(f"    [{colour}]{kpi_id}[/]: {outcome}")

    if report.skipped_kpis or any(o.startswith("ERROR") for _, o in report.dry_run):
        raise typer.Exit(code=1)


@publish_app.command("local")
def publish_local_cmd(
    plugin_dir: Path = typer.Argument(..., help="A packaged plugin directory (has .claude-plugin/)."),
    destination: Path = typer.Argument(..., help="Where to copy (or zip) the plugin to."),
    as_zip: bool = typer.Option(False, "--zip", help="Write a .zip instead of copying the directory."),
) -> None:
    from forge_core.publishing import publish_local

    result = publish_local(plugin_dir, destination, as_zip=as_zip)
    console.print(f"[bold green]Published to {result}[/]")


@publish_app.command("marketplace")
def publish_marketplace_cmd(
    plugin_dir: Path = typer.Argument(..., help="A packaged plugin directory."),
    marketplace_dir: Path = typer.Argument(..., help="Local checkout of the marketplace catalog repo."),
    marketplace_name: str = typer.Option(..., help="Marketplace catalog name (kebab-case)."),
    owner_name: str = typer.Option(..., help="Marketplace owner display name."),
    push: bool = typer.Option(
        False, help="Also push MARKETPLACE_DIR to GITHUB_TOKEN/FORGE_MARKETPLACE_REPO as one commit."
    ),
) -> None:
    from forge_core.models.plugin_spec import MarketplaceOwner
    from forge_core.publishing import get_repo, publish_to_marketplace, push_plugin_to_repo

    owner = MarketplaceOwner(name=owner_name)
    result_dir = publish_to_marketplace(
        plugin_dir, marketplace_dir, marketplace_name=marketplace_name, owner=owner
    )
    console.print(f"[bold green]Marketplace catalog updated at {result_dir}[/]")

    if push:
        repo_full_name = os.environ["FORGE_MARKETPLACE_REPO"]
        repo = get_repo(os.environ["GITHUB_TOKEN"], repo_full_name)
        source = push_plugin_to_repo(
            repo, result_dir, commit_message=f"Publish {marketplace_name}", repo_full_name=repo_full_name
        )
        console.print(f"[bold green]Pushed to {repo_full_name}@{source.sha}[/]")


@publish_app.command("github")
def publish_github_cmd(
    plugin_dir: Path = typer.Argument(..., help="A packaged plugin directory."),
    repo_name: str = typer.Option(None, help="Repo (and catalog) name. Defaults to the plugin's own name."),
    owner: str = typer.Option(
        None, help="GitHub org/user for the new repo. Defaults to GITHUB_ORG, then the token's own account."
    ),
    private: bool = typer.Option(
        False, help="Create the repo as private (needs the installer to have GitHub access to see it)."
    ),
) -> None:
    """Create a brand-new GitHub repo for PLUGIN_DIR and push it - no
    pre-existing repo or marketplace needed. The repo is immediately
    installable with the two commands this prints."""
    from forge_core.packaging import bind_http_mcp, ensure_mcp_token, hosted_mcp_url, run_id_from_plugin_dir
    from forge_core.publishing import publish_plugin_as_new_repo

    public_base = os.environ.get("FORGE_PUBLIC_BASE_URL")
    if not public_base:
        console.print(
            "[red]FORGE_PUBLIC_BASE_URL is not set. Claude Desktop cannot auto-connect a local "
            "python MCP from GitHub; set it to a public HTTPS origin that reaches the Forge API.[/]"
        )
        raise typer.Exit(code=1)
    run_id = run_id_from_plugin_dir(plugin_dir)
    if not run_id:
        console.print(
            f"[red]could not infer run id from {plugin_dir} (expected .../runs/<run_id>/output/<plugin>)[/]"
        )
        raise typer.Exit(code=1)
    bind_http_mcp(plugin_dir, hosted_mcp_url(public_base, run_id, ensure_mcp_token(plugin_dir.parent)))

    result = publish_plugin_as_new_repo(
        plugin_dir,
        token=os.environ["GITHUB_TOKEN"],
        repo_name=repo_name,
        owner=owner or os.environ.get("GITHUB_ORG"),
        private=private,
    )
    console.print(f"[bold green]Published to {result.html_url}[/]")
    console.print("Install with:")
    console.print(f"  {result.marketplace_add_command}")
    console.print(f"  {result.install_command}")


if __name__ == "__main__":
    app()
