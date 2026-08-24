"""P1-09 entry point.

    uv run python -m evals.harness.runner --dataset all --compare evals/baselines/v0.json

Generates each fixture's plugin under deterministic (--no-llm) generation -
the harness measures answering quality against the real runtime, which is
orthogonal to generation-time judgment calls (P1-04/P1-08 already have their
own dedicated tests for those, including live-LLM ones). Every P1-08 binding
question is confirmed with the resolver's own top pick, exactly like a real
caller with nothing more informed to add.

Boots the plugin's real MCP server over stdio exactly as Claude Desktop
would (same session-setup shape as validation/mcp_smoke.py - a separate
process per fixture, never imported in-process), and drives a real Gemini
model with the plugin's own generated SKILL.md as system prompt and its real
tool list - one fresh agent per question, so no context leaks between
questions the way it would in a single long chat.

Known gap, not built in this pass: an equivalent run under --llm generation
would also exercise the LLM-judged value-set resolution (fixes the
'active'==='completed' bug) and the LLM/agent binding tiers - this harness's
default run does not, since --no-llm generation is deterministic and cheap
to keep as the default CI path. Worth a second `--generation-llm` mode later;
flagged rather than silently assumed away.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from evals.harness.mcp_tools import mcp_tools_as_langchain
from evals.harness.report import build_report, diff_summaries
from evals.harness.scoring import (
    GoldenQuestion,
    QuestionResult,
    score_categorical,
    score_numeric,
    score_refusal_with_judge,
    score_tool_selection,
)
from forge_core.llm.provider import LLMProvider
from forge_core.models.common import RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASETS_ROOT = REPO_ROOT / "evals" / "datasets"

FIXTURES: dict[str, dict[str, object]] = {
    "bookings": {
        "source": REPO_ROOT / "fixtures" / "datasets" / "bookings.csv",
        "industry_override": "healthcare-diagnostics",
    },
    "edtech": {
        "source": REPO_ROOT / "fixtures" / "datasets" / "edtech.sqlite",
        "industry_override": None,
    },
    "retail_orders": {
        "source": REPO_ROOT / "fixtures" / "datasets" / "retail_orders",
        "industry_override": None,
    },
}


def _generate_plugin(name: str, out_dir: Path) -> Path:
    cfg = FIXTURES[name]
    record = RunRecord(
        run_id=f"eval-{name}",
        source_path=str(cfg["source"]),
        output_dir=str(out_dir),
        industry_override=cfg["industry_override"],
    )
    result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT)
    if result.status == RunStatus.NEEDS_INPUT and result.binding_questions:
        result.binding_confirmations = {q.role: q.physical for q in result.binding_questions}
        result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT)
    # A run can still be RunStatus.FAILED here (P1-04 legitimately rejecting
    # an implausible binding, e.g. edtech's unused score->revenue_amount) -
    # PACKAGE always runs before VALIDATE, so the plugin directory exists on
    # disk regardless and is exactly what the negative questions probe.
    package_event = next((e for e in reversed(result.events) if e.stage.value == "package"), None)
    if package_event is None:
        raise RuntimeError(f"{name}: never reached PACKAGE ({result.status.value}): {result.error}")
    return Path(package_event.data["plugin_dir"])


def _load_questions(name: str) -> list[GoldenQuestion]:
    raw = yaml.safe_load((DATASETS_ROOT / name / "questions.yaml").read_text(encoding="utf-8"))
    return [GoldenQuestion.model_validate(q) for q in raw]


def _load_skill_md(plugin_dir: Path) -> str:
    matches = list(plugin_dir.glob("skills/*/SKILL.md"))
    if not matches:
        raise FileNotFoundError(f"no SKILL.md found under {plugin_dir}/skills/*/")
    return matches[0].read_text(encoding="utf-8")


def _extract_text(content: object) -> str:
    """A LangChain AIMessage's .content is a plain string for a simple
    reply, but a list of content blocks (text, plus provider-specific
    blocks like Gemini's thinking-signature metadata) whenever the model
    used extended thinking - str()-ing that list directly leaks huge
    base64 signature blobs into the scored answer. Keep only the text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


async def _ask(lc_tools: list, skill_md: str, question: str, model_name: str) -> tuple[str, list[str]]:
    from langchain.agents import create_agent
    from langchain_google_genai import ChatGoogleGenerativeAI

    model = ChatGoogleGenerativeAI(
        model=model_name, google_api_key=os.environ.get("GEMINI_API_KEY"), temperature=0.1
    )
    # One fresh agent per question - no checkpointer, no shared thread - so
    # an earlier answer can never leak into a later question's context.
    agent = create_agent(model=model, tools=lc_tools, system_prompt=skill_md)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]}, config={"recursion_limit": 15}
    )
    messages = result["messages"]
    tool_calls: list[str] = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            tool_calls.append(call["name"])
    final_text = ""
    for m in reversed(messages):
        if getattr(m, "type", None) == "ai" and getattr(m, "content", None):
            final_text = _extract_text(m.content)
            if final_text:
                break
    return final_text, tool_calls


def _score(q: GoldenQuestion, final_answer: str, tool_calls: list[str], judge: LLMProvider | None) -> QuestionResult:
    exp = q.expects
    tool_ok = score_tool_selection(tool_calls, exp.tool)
    false_confidence = False

    if exp.behavior == "numeric":
        passed, detail = score_numeric(final_answer, float(exp.ground_truth), exp.tolerance)  # type: ignore[arg-type]
    elif exp.behavior == "categorical":
        passed, detail = score_categorical(final_answer, str(exp.ground_truth))
    else:  # refuse_or_clarify / qualify_answer - the false-confidence-rate categories
        passed, detail = score_refusal_with_judge(q.question, exp.reason, final_answer, judge)
        false_confidence = not passed

    if tool_ok is False:
        passed = False
        detail = f"{detail}; expected tool {exp.tool!r} not called (called: {tool_calls})"

    return QuestionResult(
        id=q.id,
        category=q.category,
        passed=passed,
        tool_selected_correctly=tool_ok,
        false_confidence=false_confidence,
        detail=detail,
        final_answer=final_answer,
        tool_calls=tool_calls,
    )


async def _run_dataset(
    name: str, out_dir: Path, judge: LLMProvider | None, model_name: str
) -> list[QuestionResult]:
    from mcp import StdioServerParameters, stdio_client
    from mcp.client.session import ClientSession

    plugin_dir = _generate_plugin(name, out_dir)
    skill_md = _load_skill_md(plugin_dir)
    questions = _load_questions(name)

    env = dict(os.environ)
    env["MIS_MCP_CONFIG_DIR"] = str(plugin_dir / "config")
    env["MIS_MCP_DATA_DIR"] = str(plugin_dir / "data")
    params = StdioServerParameters(command=sys.executable, args=["-m", "mis_mcp_runtime.server"], env=env)

    results: list[QuestionResult] = []
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        mcp_tools = (await session.list_tools()).tools
        lc_tools = mcp_tools_as_langchain(session, mcp_tools)

        for q in questions:
            print(f"  [{name}] {q.id}: {q.question}", file=sys.stderr)
            try:
                final_answer, tool_calls = await _ask(lc_tools, skill_md, q.question, model_name)
            except Exception as exc:  # noqa: BLE001 - a harness failure is still a scored failure
                results.append(
                    QuestionResult(id=q.id, category=q.category, passed=False, error=str(exc))
                )
                continue
            results.append(_score(q, final_answer, tool_calls, judge))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="all", choices=[*FIXTURES, "all"])
    parser.add_argument("--compare", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "baselines" / "latest.json")
    parser.add_argument("--model", default=os.environ.get("FORGE_LLM_AGENT_MODEL", "gemini-2.5-flash"))
    parser.add_argument(
        "--work-dir", type=Path, default=Path(os.environ.get("TEMP", "/tmp")) / "forge_evals"
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    from forge_core.llm import get_provider

    judge: LLMProvider | None
    try:
        judge = get_provider(role="critique")
    except Exception:  # noqa: BLE001 - no key configured: fall back to the deterministic scorer
        judge = None

    names = list(FIXTURES) if args.dataset == "all" else [args.dataset]
    all_results: dict[str, list[QuestionResult]] = {}
    for name in names:
        print(f"=== {name} ===", file=sys.stderr)
        all_results[name] = asyncio.run(_run_dataset(name, args.work_dir / name, judge, args.model))

    report = build_report(all_results, generated_at=datetime.now(UTC).isoformat())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))

    if args.compare and args.compare.exists():
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        print("\n--- vs baseline ---")
        print(json.dumps(diff_summaries(baseline["summary"], report["summary"]), indent=2))


if __name__ == "__main__":
    main()
