"""A real LangChain (LangGraph-backed `create_agent`) agent that understands
a customer's whole schema before the pipeline ever asks the customer a
question about it: reads real column values and searches unfamiliar
business terms to propose what each non-obvious column means and which
industry the data most likely belongs to.

This is the `use_agent=True` path for Stage 2's semantic profiling (see
`profiling/semantic.py`) — opt-in, the same flag the binding agent already
uses. `use_agent=False` keeps the cheap single-shot call. Output feeds two
existing, unrelated pieces of machinery without either needing to change:
- Low-confidence `column_semantics` entries become clarification questions
  through the merged pause (`profiling/quality.py::build_data_review`).
- `suggested_industry` is shown next to the deterministic classifier's own
  ranked matches during the industry pause — advisory only, it never picks
  a pack itself (see `classification/matcher.py`, untouched by this).

Same safety invariant as the binding agent: it can look and reason, but a
column name or pack slug it submits is re-validated against what's real
before being trusted; nothing it says is taken on faith.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from forge_core.agentic.tools import build_data_understanding_tools
from forge_core.llm.provider import AgentCallRecorder
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import ColumnSemantic, IndustryGuess, StructuralProfile
from forge_core.profiling.semantic import (
    MAX_SAMPLE_ROWS_PER_TABLE,
    REDACTED,
    _compact_structural,
    _redacted_samples,
)

logger = logging.getLogger("forge_core.agentic.data_agent")

DEFAULT_AGENT_MODEL = "gemini-3.7-flash"
MAX_AGENT_STEPS = 30

_SYSTEM_PROMPT_TEMPLATE = """You are a careful data analyst meeting a customer's business data for the \
first time, before an automated MIS/BI plugin is built on top of it. Two things depend on getting \
this right: which industry template gets used, and which columns need a human's explanation before \
anything is built.

STRUCTURAL FACTS (ground truth - every table/column reference you use MUST come from here):
{structural}

SAMPLE ROWS (redacted, at most {max_rows} rows per table - PII-flagged fields already replaced with \
"{redacted}"; never assume you know a redacted value):
{samples}

CANDIDATE INDUSTRIES (pick one of these slugs, or null if none plausibly fit):
{industries}

Work through this:
1. For each column that ISN'T obvious from its name (skip things like an "email" column literally \
named email), form a hypothesis about what it means. If the structural facts and sample rows alone \
don't make it clear, call preview_column_values to see more real values, or \
search_industry_terminology if the ambiguity is about an unfamiliar business term - before settling \
on a low-confidence guess. Then call submit_column_meaning once for that column with your best \
meaning and an honest confidence (0.0-1.0). A genuinely unclear column should get a LOW confidence \
score, not a confident-sounding guess - low confidence is what tells the system to ask the customer, \
which is the correct outcome for a column you truly can't determine.
2. Once you've formed a view of the data, call submit_industry_guess exactly once with the best-fit \
slug from the list above (or null), your confidence, and reasoning grounded in specific column names \
and values you actually saw - not a generic guess.
3. You do not need to call submit_column_meaning for every column - only ones worth commenting on. \
Stop once you've covered the non-obvious columns and submitted your industry guess."""


def _industries_block(packs: list[IndustryPack]) -> str:
    return "\n".join(f"- {p.slug}: {p.name} - {p.description}" for p in packs)


def run_data_understanding_agent(
    data_source: DataSource,
    structural: StructuralProfile,
    packs: list[IndustryPack],
    *,
    model_name: str | None = None,
    on_stats: Callable[[dict], None] | None = None,
) -> tuple[list[ColumnSemantic], IndustryGuess | None]:
    """Returns (column_semantics, suggested_industry). Never raises - any
    agent/tool/network failure degrades to ([], None), the same outcome as
    not having a semantic profile at all.

    `on_stats`, when given, receives this single invocation's token/step/tool
    accounting (see `AgentCallRecorder.summary`) even when the agent fails."""
    valid_columns = {(c.table, c.name) for c in structural.columns}
    valid_slugs = {p.slug for p in packs}

    column_semantics: list[ColumnSemantic] = []
    industry_guess: dict[str, Any] = {}

    def submit_column_meaning(table: str, column: str, proposed_meaning: str, confidence: float) -> str:
        """Record your understanding of one column. Call once per non-obvious
        column. `table`/`column` must be copied exactly from the structural
        facts above. `confidence` is your own honest 0.0-1.0 self-assessment -
        a low score for a column you're genuinely unsure about is a correct
        and useful answer, not a failure."""
        if (table, column) not in valid_columns:
            return f"ERROR: {table}.{column} is not a real table/column. Check the structural facts."
        column_semantics.append(
            ColumnSemantic(table=table, column=column, proposed_meaning=proposed_meaning, confidence=confidence)
        )
        return "Recorded."

    def submit_industry_guess(pack_slug_guess: str | None, confidence: float, reasoning: str) -> str:
        """Submit your industry guess. Call exactly once, as your last
        action. `pack_slug_guess` must be copied exactly from the candidate
        industries list, or null if none plausibly fit."""
        industry_guess.update(pack_slug_guess=pack_slug_guess, confidence=confidence, reasoning=reasoning)
        return "Recorded."

    # Cheap path first: one structured call over the data map.
    #
    # This agent was measured at 39,109 tokens across 10 calls - 51% of an
    # entire build, more than every other component combined - to produce a
    # sentence per column and one industry guess. Both are judgements over
    # facts the data map already states (type, cardinality, null rate, range,
    # top values), not investigations, so the tool loop was spending its
    # budget re-reading what it had already been handed.
    single = _understand_from_map_single_call(
        structural, packs, model_name=model_name, on_stats=on_stats
    )
    if single is not None:
        return single

    recorder = AgentCallRecorder()
    try:
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_google_genai import ChatGoogleGenerativeAI

        tools = [
            *build_data_understanding_tools(structural, data_source),
            StructuredTool.from_function(submit_column_meaning),
            StructuredTool.from_function(submit_industry_guess),
        ]
        model = ChatGoogleGenerativeAI(
            model=model_name or os.environ.get("FORGE_LLM_AGENT_MODEL", DEFAULT_AGENT_MODEL),
            google_api_key=os.environ.get("GEMINI_API_KEY"),
            temperature=0.1,
        )
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            structural=json.dumps(_compact_structural(structural), indent=2, default=str),
            samples=json.dumps(_redacted_samples(data_source, structural), indent=2, default=str),
            max_rows=MAX_SAMPLE_ROWS_PER_TABLE,
            redacted=REDACTED,
            industries=_industries_block(packs),
        )
        agent = create_agent(model=model, tools=tools, system_prompt=system_prompt)
        agent.invoke(
            {"messages": [{"role": "user", "content": "Understand this data now."}]},
            config={"recursion_limit": MAX_AGENT_STEPS, "callbacks": [recorder]},
        )
    except Exception:  # noqa: BLE001 - any agent/tool/network failure degrades to "no semantic profile"
        if on_stats is not None:
            on_stats(recorder.summary())
        return [], None

    if on_stats is not None:
        on_stats(recorder.summary())

    guess = None
    slug = industry_guess.get("pack_slug_guess")
    if industry_guess and (slug is None or slug in valid_slugs):
        guess = IndustryGuess(
            pack_slug_guess=slug,
            confidence=industry_guess.get("confidence", 0.0) or 0.0,
            reasoning=industry_guess.get("reasoning", "") or "",
        )
    return column_semantics, guess


__all__ = ["run_data_understanding_agent"]


_SINGLE_CALL_PROMPT = """\
You are reading a profile of a customer's dataset that has already been
measured, and writing down what each column means in plain business terms.

MEASURED PROFILE (authoritative - every number is a real observation):
{data_map_block}

CANDIDATE INDUSTRIES:
{industries}

Return JSON:

{{"columns": [{{"table": "...", "column": "...",
               "meaning": "<one short sentence, plain business language>",
               "confidence": 0.8}}],
  "industry": {{"pack_slug": "<exact slug from the list above, or null>",
                "confidence": 0.0,
                "reasoning": "<why, citing what you saw in the profile>"}}}}

Rules:
- "confidence" is a NUMBER between 0 and 1, never a word like "high".
- Cover every column that is not self-explanatory. Skip ones where the
  meaning adds nothing beyond the name.
- Judge from the measured VALUES, not the column name. A column named
  nothing like "revenue" can be revenue if its distribution looks like
  money; one that matches by name can still be something else entirely.
- "pack_slug" must be copied exactly from the candidate list, or null if
  none genuinely fit. Null is a correct answer - a confidently wrong
  industry is worse than none, because downstream treats it as evidence.
"""


def _understand_from_map_single_call(
    structural: StructuralProfile,
    packs: list[IndustryPack],
    *,
    model_name: str | None,
    on_stats: Callable[[dict], None] | None,
) -> tuple[list[ColumnSemantic], IndustryGuess | None] | None:
    """One structured call over the data map. Returns None (not an empty
    result) when it could not run or produced nothing, so the caller can fall
    back to the tool-using agent rather than silently shipping no semantics."""
    if structural.data_map is None:
        return None

    valid_columns = {(c.table, c.name) for c in structural.columns}
    valid_slugs = {p.slug for p in packs}
    started = time.monotonic()
    try:
        from forge_core.llm import get_provider

        provider = get_provider(role="agent")
        raw = provider.generate_json(
            _SINGLE_CALL_PROMPT.format(
                data_map_block=structural.data_map.to_prompt(),
                industries=_industries_block(packs),
            )
        )
        usage = provider.drain_usage() if hasattr(provider, "drain_usage") else {}
    except Exception as exc:  # noqa: BLE001 - falls back to the agent below
        logger.warning("Single-call data understanding failed: %s", exc)
        return None

    if on_stats is not None:
        on_stats(
            {
                "steps": usage.get("llm_calls", 1),
                "tool_calls": 0,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "thinking_tokens": usage.get("thinking_tokens", 0),
                "wall_seconds": round(time.monotonic() - started, 3),
            }
        )

    semantics: list[ColumnSemantic] = []
    for item in (raw or {}).get("columns") or []:
        try:
            table, column = item["table"], item["column"]
            if (table, column) not in valid_columns:
                continue  # never let a hallucinated column into the profile
            semantics.append(
                ColumnSemantic(
                    table=table,
                    column=column,
                    proposed_meaning=str(item.get("meaning", "")),
                    confidence=_confidence(item.get("confidence")),
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad row must not lose the rest
            logger.debug("Skipping malformed column semantic %r: %s", item, exc)

    guess = None
    industry = (raw or {}).get("industry") or {}
    slug = industry.get("pack_slug")
    if isinstance(slug, str) and slug.strip().lower() in ("null", "none", ""):
        slug = None
    if slug is None or slug in valid_slugs:
        guess = IndustryGuess(
            pack_slug_guess=slug,
            confidence=_confidence(industry.get("confidence")) if slug else 0.0,
            reasoning=str(industry.get("reasoning", "")),
        )

    if not semantics and guess is None:
        return None
    return semantics, guess


_WORD_CONFIDENCE = {"high": 0.85, "medium": 0.6, "moderate": 0.6, "low": 0.35}


def _confidence(value: Any) -> float:
    """Models return "high" as readily as 0.85 however the prompt asks."""
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _WORD_CONFIDENCE:
            return _WORD_CONFIDENCE[text]
        try:
            return max(0.0, min(1.0, float(text)))
        except ValueError:
            pass
    return 0.6
