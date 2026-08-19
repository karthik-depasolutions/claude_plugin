"""A real LangChain (LangGraph-backed `create_agent`) reasoning agent for the
one binding decision that's genuinely hard to get right from column names
alone: which physical column represents a given canonical business role.

This is the last-resort tier in `binding/resolver.py`'s resolution order —
tried only for roles the deterministic scorer *and* the single-shot LLM
proposer (`binding/llm_proposer.py`) both failed to resolve, and only when a
caller opts in (`use_agent=True`). Unlike the single-shot proposer, this
agent can inspect real sample data and search the web for how a term is
normally used *before* committing to an answer, and has to justify its
choice — but the safety invariant from the plan doc doesn't change: it must
still name a column that actually exists on the table, or the caller (never
this module) rejects it. An agent that reasons well is still not trusted to
invent a column.

Memory (see `memory.py`): an exact-schema repeat (e.g. resuming a paused
run) skips the agent entirely via the decision cache; a same-role,
different-schema case gets past decisions as reference examples instead of
starting from a blank slate every time. Every invocation's full
message/tool-call trace is also persisted (thread_id logged below) purely
for later audit - never read back into a decision.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Callable

from forge_core.agentic import memory
from forge_core.agentic.tools import build_binding_tools
from forge_core.llm.provider import AgentCallRecorder
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile

DEFAULT_AGENT_MODEL = "gemini-2.5-flash"
MAX_AGENT_STEPS = 12

_SYSTEM_PROMPT_TEMPLATE = """You are a careful data engineer binding one canonical business \
concept to a real physical column on a customer's table, for an automated MIS/BI plugin \
generator. Getting this wrong means every KPI built on top of it is wrong, so back your \
final answer with real evidence, not just a plausible-sounding column name.

Concept to bind: "{role}"
What it means: {description}
{examples_block}
Work through this before answering:
1. Call list_candidate_columns to see every column actually available - never consider a \
column that isn't on that list.
2. If more than one column's name is plausible, or none obviously fit, call \
preview_column_values on the strongest candidate(s) to check the column's real content, \
not just its name.
3. If the business terminology itself is ambiguous (e.g. an unfamiliar industry term), call \
search_industry_terminology to ground your decision in how the term is actually used.
4. Call finalize_binding exactly once, as your last action, with your decision. `column` \
must be copied character-for-character from the real column list, or null if none of them \
represent this concept - inventing a plausible-looking name is a hard failure, worse than \
saying null."""


def _name_tokens(column: str) -> list[str]:
    """The column name split into its constituent word tokens (snake_case,
    camelCase, kebab-case, spaces) - used in few-shot examples instead of
    the verbatim name, so a prompt/trace never carries another customer's
    exact column identifier."""
    return [w for w in re.split(r"[^a-z0-9]+", column.lower()) if w]


def _examples_block(examples: list[memory.CachedDecision]) -> str:
    if not examples:
        return ""
    lines = "\n".join(
        f"- A previous schema resolved this concept to a column whose name contained "
        f"{_name_tokens(ex.column)!r} and whose values looked like: {ex.value_shape}"
        for ex in examples
    )
    return (
        "\nFor reference, here's how this same concept was resolved on other schemas "
        "(different column names - never assume this customer's column is named the same, "
        f"only use these as reasoning examples):\n{lines}\n"
    )


def propose_binding_with_agent(
    role: str,
    description: str,
    table_cols: list[ColumnProfile],
    source: DataSource,
    fact_table_physical_ref: str,
    *,
    pack_slug: str = "unknown-pack",
    tenant_id: str,
    model_name: str | None = None,
    context_extra: str = "",
    on_stats: Callable[[dict], None] | None = None,
) -> str | None:
    """Returns the chosen column name, or `None` if the agent found nothing
    that fits (or failed for any reason - a broken agent should degrade to
    "unresolved", the same outcome as the tiers before it, never raise).

    `tenant_id` scopes every memory read/write to this customer: the exact-
    decision cache and few-shot examples never touch another tenant's rows
    (see memory.py), so Customer B's agent never sees Customer A's columns.

    `on_stats`, when given, receives this single invocation's token/step/tool
    accounting (see `AgentCallRecorder.summary`) even when the agent fails -
    the orchestrator turns it into a StageEvent so agent LLM spend is visible
    in run telemetry instead of being an unexplained latency gap."""
    valid_names = {c.name for c in table_cols}
    # `context_extra` (data-review answers, when supplied) is folded into
    # the fingerprint so a cached decision is invalidated by new user
    # context - otherwise the cache short-circuit below would return a stale
    # answer before the user's notes ever reached the prompt.
    fingerprint = memory.schema_fingerprint(table_cols, extra=context_extra)

    cached = memory.get_exact_decision(pack_slug, role, fingerprint, tenant_id)
    if cached is not None:
        return cached.column if cached.column in valid_names else None

    captured: dict[str, Any] = {}

    def finalize_binding(column: str | None, confidence: float, reasoning: str) -> str:
        """Submit your final decision. Call this exactly once, as your last
        action. `column` must be copied exactly from list_candidate_columns's
        output, or null if none of the real columns represent this concept.
        `confidence` is your own 0.0-1.0 self-assessment. `reasoning` is a
        one or two sentence justification citing the evidence you gathered
        (tool outputs), not just a restatement of the column name."""
        captured.update(column=column, confidence=confidence, reasoning=reasoning)
        return "Recorded."

    thread_id = f"{pack_slug}:{role}:{fingerprint}:{uuid.uuid4().hex[:8]}"
    recorder = AgentCallRecorder()
    try:
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_google_genai import ChatGoogleGenerativeAI

        tools = [
            *build_binding_tools(table_cols, source, fact_table_physical_ref),
            StructuredTool.from_function(finalize_binding),
        ]
        model = ChatGoogleGenerativeAI(
            model=model_name or os.environ.get("FORGE_LLM_AGENT_MODEL", DEFAULT_AGENT_MODEL),
            google_api_key=os.environ.get("GEMINI_API_KEY"),
            temperature=0.1,
        )
        examples = memory.recent_examples(
            pack_slug, role, tenant_id=tenant_id, exclude_fingerprint=fingerprint
        )
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            role=role, description=description, examples_block=_examples_block(examples)
        )
        with memory.trace_checkpointer() as checkpointer:
            agent = create_agent(model=model, tools=tools, system_prompt=system_prompt, checkpointer=checkpointer)
            agent.invoke(
                {"messages": [{"role": "user", "content": f"Bind the concept {role!r} now."}]},
                config={
                    "recursion_limit": MAX_AGENT_STEPS,
                    "configurable": {"thread_id": thread_id},
                    "callbacks": [recorder],
                },
            )
    except Exception:  # noqa: BLE001 - any agent/tool/network failure degrades to "unresolved"
        if on_stats is not None:
            on_stats(recorder.summary())
        return None

    if on_stats is not None:
        on_stats(recorder.summary())

    column = captured.get("column")
    confidence = captured.get("confidence", 0.0) or 0.0
    reasoning = captured.get("reasoning", "") or ""
    value_shape = ""
    if column and column in valid_names:
        chosen = next(c for c in table_cols if c.name == column)
        value_shape = memory.value_shape_of(chosen)
    if not column or column not in valid_names:
        memory.record_decision(pack_slug, role, fingerprint, None, confidence, reasoning, tenant_id)
        return None

    memory.record_decision(
        pack_slug, role, fingerprint, column, confidence, reasoning, tenant_id, value_shape=value_shape
    )
    return column


__all__ = ["propose_binding_with_agent"]
