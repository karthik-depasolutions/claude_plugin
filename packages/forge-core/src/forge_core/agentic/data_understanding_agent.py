"""P2-05 — the data-understanding agent. One bounded session per
`resolve_bindings()` call (not one per role - see P2-03's own rationale: a
tool call per column on a large schema is thousands of round trips). The
agent receives the precomputed `DataMap` up front and only needs to drill
into `ambiguous_columns` with the P2-04 tools, then propose a `ColumnClaim`
per canonical role it can confidently resolve.

This is tier 1 for `use_agent=True` runs, ahead of the deterministic scorer
(binding/scorer.py) - but it is not trusted blindly: every claim goes
through P2-06's gates (V1-V3) before `binding/resolver.py` ever accepts it,
with one retry (the gate's failure reason fed back as evidence) before the
role falls through to the existing deterministic/LLM-proposer/legacy-agent
tier chain unchanged. A role this agent doesn't address, or addresses but
fails verification twice, is simply absent from its result - never a hard
failure, and never silently trusted either.

`use_agent=False` runs (or a broken agent, missing API key, network error)
never reach this module at all - the tier chain built for P1-08 is untouched
and remains the entire binding pipeline for those runs.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from forge_core.agentic import memory
from forge_core.agentic.investigation_tools import build_investigation_tools
from forge_core.llm.provider import AgentCallRecorder
from forge_core.models.claims import ColumnClaim
from forge_core.models.data_map import DataMap
from forge_core.models.datasource import DataSource
from forge_core.models.metrics import AggOp
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile
from forge_core.validation.gates import GateVerdict, verify_column_claim

DEFAULT_AGENT_MODEL = "gemini-2.5-flash"
MAX_AGENT_STEPS = 15
MAX_ATTEMPTS = 2

_SYSTEM_PROMPT_TEMPLATE = """You are a careful data engineer binding canonical business concepts \
to real physical columns on a customer's data, for an automated MIS/BI plugin generator. Getting \
this wrong means every metric built on top is wrong - a student test score reported as revenue is \
the exact failure this process exists to prevent. Back every claim with real evidence, never a \
plausible-sounding column name alone.

Concepts that still need a column bound to them:
{roles_block}

The data map below already computed distribution statistics, format fingerprints, and top values \
for every column - most columns are unambiguous from this alone. Columns marked [AMBIGUOUS] are \
the ones worth spending tool calls on; everything else you can likely decide from the map directly.

{data_map_block}
{retry_feedback}
Work through this:
1. For each concept, decide whether an existing column in the data map represents it. Use the real \
statistics (min/max/percentiles/top_values), not just the column name - a column named nothing like \
"revenue" can still be revenue if its distribution looks like money, and a column that superficially \
matches by name can still be wrong if its distribution doesn't (a 0-100-bounded number is a score, \
not currency, no matter what the concept is called).
2. For any column marked [AMBIGUOUS] or where you're still unsure, call inspect_column, \
compare_columns, or sample_rows to look closer before deciding - never guess when a tool call would \
settle it.
3. Call propose_binding once for every concept you can confidently resolve, citing the SPECIFIC \
evidence (numbers, tool output) that justifies it - not a restatement of your conclusion. Skip a \
concept entirely (call it zero times) rather than guess when the data map and tools genuinely don't \
support a confident answer - an unanswered concept falls through to other resolution methods, which \
is the correct outcome, not a failure.
4. Call finish exactly once, as your last action, after you've called propose_binding for every \
concept you're confident about."""


def _roles_block(roles: dict[str, str]) -> str:
    return "\n".join(f'- "{role}": {description}' for role, description in roles.items())


def _retry_feedback_block(feedback: dict[str, str]) -> str:
    if not feedback:
        return ""
    lines = "\n".join(f'- "{role}": {reason}' for role, reason in feedback.items())
    return (
        "\nYour previous attempt at some of these concepts was rejected by an automated check. "
        "Reconsider these specifically, using real tool evidence this time:\n" + lines + "\n"
    )


def propose_bindings_with_agent(
    roles: dict[str, str],
    table_cols: list[ColumnProfile],
    data_map: DataMap | None,
    source: DataSource,
    structural: StructuralProfile,
    denied_columns: set[str],
    *,
    pack_slug: str = "unknown-pack",
    tenant_id: str,
    model_name: str | None = None,
    on_stats: Callable[[dict], None] | None = None,
) -> dict[str, tuple[str, ColumnClaim]]:
    """Returns role -> (physical_column, verified ColumnClaim) only for
    roles this agent both proposed AND whose claim passed P2-06's gates
    (with up to MAX_ATTEMPTS tries, failure feedback fed back in). Roles
    absent from the result fall through to the existing tier chain in
    `binding/resolver.py` unchanged - this function never raises and never
    blocks a role it can't confidently resolve."""
    if not roles:
        return {}

    valid_names = {c.name for c in table_cols}
    col_by_name = {c.name: c for c in table_cols}
    resolved: dict[str, tuple[str, ColumnClaim]] = {}
    remaining = dict(roles)
    feedback: dict[str, str] = {}

    for attempt in range(MAX_ATTEMPTS):
        if not remaining:
            break
        claims, evidence_log = _run_one_pass(
            remaining, source, structural, denied_columns, pack_slug=pack_slug,
            tenant_id=tenant_id, model_name=model_name, on_stats=on_stats,
            retry_feedback=feedback if attempt > 0 else {},
        )
        feedback = {}
        for role, (physical_column, claim) in claims.items():
            if role not in remaining or physical_column not in valid_names:
                continue
            col = col_by_name[physical_column]
            verdict = verify_column_claim(claim, col, evidence_log)
            if verdict.verdict == GateVerdict.VERIFIED:
                resolved[role] = (physical_column, claim)
                del remaining[role]
            else:
                feedback[role] = "; ".join(verdict.reasons) or "verification failed"
        # Roles the agent never addressed this pass simply carry over to the
        # next attempt with no feedback (nothing to react to yet).

    return resolved


def _run_one_pass(
    roles: dict[str, str],
    source: DataSource,
    structural: StructuralProfile,
    denied_columns: set[str],
    *,
    pack_slug: str,
    tenant_id: str,
    model_name: str | None,
    on_stats: Callable[[dict], None] | None,
    retry_feedback: dict[str, str],
) -> tuple[dict[str, tuple[str, ColumnClaim]], list[str]]:
    captured: dict[str, dict[str, Any]] = {}
    evidence_log: list[str] = []

    def propose_binding(
        role: str,
        column: str,
        table: str,
        meaning: str,
        kind: str,
        unit: str | None,
        valid_aggregations: list[str],
        confidence: float,
        evidence: list[str],
    ) -> str:
        """Propose that `column` (on `table`) represents `role`. `kind` is
        one of identifier/measure/dimension/time/flag/free_text. `unit`
        describes what the values mean (e.g. "INR", "percent", "score",
        "count") or null if not applicable. `valid_aggregations` is a list
        from: sum, mean, min, max, count, nunique, std, var, median - only
        the ones that are mathematically sound for this column (e.g. never
        "sum" for a percentage or score). `evidence` must be specific
        numbers or facts a tool actually returned - copy them, don't
        paraphrase. Call this once per concept; do not call it again for a
        role you've already proposed in this same conversation."""
        captured[role] = dict(
            column=column, table=table, meaning=meaning, kind=kind, unit=unit,
            valid_aggregations=valid_aggregations, confidence=confidence, evidence=evidence,
        )
        return f"Recorded proposal for {role!r}."

    def finish() -> str:
        """Call this once, as your last action, after proposing bindings for
        every concept you're confident about."""
        return "Done."

    recorder = AgentCallRecorder()
    data_map_block = (
        structural.data_map.to_prompt() if structural.data_map is not None else "(no data map available)"
    )
    # V1 (evidence exists) must accept facts cited straight from the data
    # map, not only from a tool call this session - that's the whole point
    # of precomputing it (P2-03): most columns are decided from the map
    # alone, with tools reserved for the ambiguous minority. Seeding the log
    # with the map itself means "X.role=currency" is real evidence whether
    # the agent read it off the map or called inspect_column to confirm it.
    evidence_log.append(data_map_block)
    try:
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_google_genai import ChatGoogleGenerativeAI

        tools = [
            *build_investigation_tools(source, structural, denied_columns, evidence_sink=evidence_log),
            StructuredTool.from_function(propose_binding),
            StructuredTool.from_function(finish),
        ]
        model = ChatGoogleGenerativeAI(
            model=model_name or os.environ.get("FORGE_LLM_AGENT_MODEL", DEFAULT_AGENT_MODEL),
            google_api_key=os.environ.get("GEMINI_API_KEY"),
            temperature=0.1,
        )
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            roles_block=_roles_block(roles),
            data_map_block=data_map_block,
            retry_feedback=_retry_feedback_block(retry_feedback),
        )
        agent = create_agent(model=model, tools=tools, system_prompt=system_prompt)
        agent.invoke(
            {"messages": [{"role": "user", "content": "Resolve every concept you can now."}]},
            config={"recursion_limit": MAX_AGENT_STEPS, "callbacks": [recorder]},
        )
    except Exception:  # noqa: BLE001 - any agent/tool/network failure yields nothing, never raises
        if on_stats is not None:
            on_stats(recorder.summary())
        return {}, evidence_log

    if on_stats is not None:
        on_stats(recorder.summary())

    result: dict[str, tuple[str, ColumnClaim]] = {}
    for role, data in captured.items():
        try:
            unit = data["unit"]
            # Some models emit the JSON string "null"/"none" instead of an
            # actual null for an optional field - normalize both to Python
            # None rather than let "null" silently fail to match any of V2's
            # known unit checks (gates.py) and pass through unverified.
            if isinstance(unit, str) and unit.strip().lower() in ("null", "none", ""):
                unit = None
            claim = ColumnClaim(
                table=data["table"],
                column=data["column"],
                meaning=data["meaning"],
                kind=data["kind"],
                unit=unit,
                valid_aggregations=[AggOp(a) for a in data["valid_aggregations"] if a in AggOp._value2member_map_],
                confidence=max(0.0, min(1.0, float(data["confidence"]))),
                evidence=list(data["evidence"] or []),
            )
        except Exception:  # noqa: BLE001 - a malformed proposal is dropped, not trusted
            continue
        result[role] = (data["column"], claim)
    return result, evidence_log


__all__ = ["propose_bindings_with_agent"]
