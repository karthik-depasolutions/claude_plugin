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
from forge_core.llm import get_provider
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


@app.command()
def run(
    source: str = typer.Argument(
        ...,
        help="Path to a data file, directory, or SQLite database - or a postgresql:// connection string.",
    ),
    output_dir: Path = typer.Option(Path("generated"), "--out", help="Where to write the plugin."),
    pack: str | None = typer.Option(
        None, "--pack", help="Industry pack slug to force (skips auto-classification)."
    ),
    use_llm: bool = typer.Option(
        True, "--llm/--no-llm", help="Use Gemini for semantic profiling/generation/critique."
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
    )

    profiling_provider = get_provider(role="profiling") if use_llm else None
    generation_provider = get_provider(role="generation") if use_llm else None
    critique_provider = get_provider(role="critique") if use_llm else None

    with Live(_render_checklist(record), console=console, refresh_per_second=8) as live:
        record.on_event(lambda _event: live.update(_render_checklist(record)))
        result = run_pipeline(
            record,
            packs_root=DEFAULT_PACKS_ROOT,
            profiling_provider=profiling_provider,
            generation_provider=generation_provider,
            critique_provider=critique_provider,
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


if __name__ == "__main__":
    app()
