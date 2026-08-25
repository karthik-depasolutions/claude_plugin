"""`forge` - the CLI entry point for running the generator pipeline
in-process, without the API/web UI. Wraps `forge_core.orchestrator` with a
live-updating checklist (via `RunRecord.on_event`) so progress is visible in
real time, then prints the full chronological event log once it finishes.
"""

from __future__ import annotations

import json
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


app = typer.Typer(help="Data2plugin - generate a Claude Code plugin from MIS data.")
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
    use_agent: bool = typer.Option(
        True,
        "--agent/--no-agent",
        help="Use a tool-using LangChain agent (schema inspection, live data preview, web "
        "search) to ground every column's meaning in real values rather than a column-name "
        "guess - the default for a real run. --no-agent is the deterministic-only path kept "
        "for offline/CI use; it never guesses a semantic role from a name either, it just "
        "leaves more roles unresolved (needs_confirmation) without the agent's investigation.",
    ),
    label: str | None = typer.Option(
        None,
        "--label",
        help="Optional project/business name (e.g. 'Sparda Music Academy') - personalizes the "
        "plugin's displayName and on-disk/repo name instead of the generic '<pack>-mis-plugin'.",
    ),
    review: bool = typer.Option(
        False,
        "--review/--no-review",
        help="Pause after profiling to review data-quality findings and answer LLM-generated "
        "questions (default off: findings are computed and printed, never paused over).",
    ),
    answers: Path | None = typer.Option(
        None,
        "--answers",
        help="Path to a JSON file mapping data-review question ids to answers, from a previous "
        "--review run - runs straight through with those answers threaded into the plugin.",
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
    # One expression drives the whole pause/answer matrix - see the plan:
    # (default) data_answers={} -> findings computed & printed, no LLM
    # question call, never pauses. --review -> data_answers=None -> questions
    # generated, pauses, prints report, exit 2. --answers f.json -> the dict
    # -> runs straight through with answers threaded.
    data_answers: dict[str, str] | None = (
        # utf-8-sig: Windows editors (Notepad, PowerShell) often write a BOM
        # when saving a user-authored answers file.
        json.loads(answers.read_text(encoding="utf-8-sig")) if answers else (None if review else {})
    )
    record = RunRecord(
        run_id=run_id,
        source_path=source_for_run,
        output_dir=str(output_dir.resolve()),
        industry_override=pack,
        label=label,
        data_answers=data_answers,
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
            use_agent=use_agent,
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
        pause_event = next(
            (e for e in reversed(result.events) if e.stage == RunStage.CLASSIFY and "needs_answers" in e.data),
            None,
        )
        # Fall back to "assume industry" (the pre-merged-pause behaviour) if
        # the pause event is ever missing - never silently print nothing.
        needs_industry = pause_event.data["needs_industry"] if pause_event else True
        needs_answers = pause_event.data["needs_answers"] if pause_event else False

        if needs_answers:
            review_event = next(
                (e for e in reversed(result.events) if e.stage == RunStage.PROFILE and "review" in e.data), None
            )
            review = review_event.data["review"] if review_event else {}
            findings = review.get("findings", [])
            if findings:
                console.print("[yellow]Data-quality review:[/]")
            for finding in findings:
                console.print(
                    f"  [{finding['severity']}] {finding['table']}."
                    f"{finding['column']}: {finding['summary']}"
                )
            for question in review.get("questions", []):
                console.print(f"  [bold]?[/] {question['question']}")
            if review.get("questions"):
                console.print(
                    "  [dim]Answer keys (use in --answers <file.json>):[/]"
                )
                for question in review.get("questions", []):
                    console.print(f"  [dim]  {question['id']}[/]")

        if needs_industry:
            console.print(
                "[yellow]Needs input:[/] industry classification was ambiguous. "
                "Re-run with --pack <slug> once you've picked one from the classify event above."
            )
        if needs_answers:
            console.print(
                "[yellow]Needs input:[/] answer the questions above via --answers <file.json> next run."
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
