"""The Context Discovery Agent: Discovers technical and business semantics from data.

In accordance with DATA2PLUGIN_CONTEXT_DISCOVERY_AGENT_IMPLEMENTATION.md:
- Inspects schema, profile, distributions, and repeated identifiers.
- Separates confirmed facts, inferred hypotheses, and open questions.
- Formulates evidence-grounded questions for critical business ambiguities.
- Evaluates the readiness gate for downstream stages.
- Produces an authoritative BusinessContext artifact.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from forge_core.agentic.context_tools import build_context_discovery_tools
from forge_core.agentic.graph.context_discovery_graph import (
    _analyze_structural_evidence,
    _build_business_context_model,
)
from forge_core.agentic.prompts.context_discovery import CONTEXT_DISCOVERY_SYSTEM_PROMPT
from forge_core.agentic.schemas.business_context import (
    BusinessAnswer,
    BusinessContext,
    BusinessQuestion,
)
from forge_core.llm import resolve_model
from forge_core.llm.provider import AgentCallRecorder
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.quality import DataQuestion, DataReview
from forge_core.profiling.quality import GENERAL_NOTES_ID
from forge_core.models.schema_profile import StructuralProfile

logger = logging.getLogger("forge_core.agentic.agents.context_discovery")

DEFAULT_AGENT_MODEL = "gemini-3.7-flash"
MAX_AGENT_STEPS = 25


def _industries_block(packs: list[IndustryPack]) -> str:
    return "\n".join(f"- {p.slug}: {p.name} - {p.description}" for p in packs)


def run_context_discovery_agent(
    data_source: DataSource,
    structural: StructuralProfile,
    packs: list[IndustryPack],
    answers: list[BusinessAnswer] | None = None,
    *,
    denied_columns: set[str] | None = None,
    model_name: str | None = None,
    on_stats: Callable[[dict], None] | None = None,
) -> BusinessContext:
    """Executes the Context Discovery Agent and returns the authoritative BusinessContext."""
    # 1. Mine baseline evidence, hypotheses, and questions deterministically
    evidence, hypotheses, questions, dq_issues = _analyze_structural_evidence(
        data_source=data_source,
        structural=structural,
        packs=packs,
    )

    domain_guess = None
    domain_confidence = 0.0
    discovered_findings: dict[str, Any] = {}
    agent_error: str | None = None

    # 2. If an LLM is available, perform semantic exploration and domain hypothesis refinement
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        recorder = AgentCallRecorder()
        try:
            from langchain.agents import create_agent
            from langchain_core.tools import StructuredTool
            from langchain_google_genai import ChatGoogleGenerativeAI

            def submit_context_findings(
                domain_guess: str | None = None,
                confidence: float = 0.9,
                reasoning: str = "",
                record_grain: str = "",
                primary_entity: str = "",
            ) -> str:
                """Submit your final business context findings once you have inspected the key columns.
                Call this tool EXACTLY ONCE as your final action to finish analysis.
                - domain_guess: Candidate industry slug (e.g. 'edtech', 'retail', 'healthcare', etc.), or null if none fit.
                - confidence: Confidence in this classification (0.0 to 1.0).
                - reasoning: Concise, evidence-based justification for this industry classification.
                - record_grain: Inferred row grain (e.g. 'One interaction/call per lead').
                - primary_entity: Main entity represented (e.g. 'Lead', 'Order', 'Patient').
                """
                discovered_findings.update(
                    domain_guess=domain_guess,
                    confidence=confidence,
                    reasoning=reasoning,
                    record_grain=record_grain,
                    primary_entity=primary_entity,
                )
                return "Business context findings recorded successfully. Analysis complete."

            tools = [
                *build_context_discovery_tools(
                    data_source=data_source,
                    structural=structural,
                    denied_columns=denied_columns,
                ),
                StructuredTool.from_function(submit_context_findings),
            ]

            model = ChatGoogleGenerativeAI(
                model=model_name or resolve_model("agent"),
                google_api_key=api_key,
                temperature=0.1,
            )

            # Hand over everything profiling already measured - every
            # column's type, cardinality, null rate, range and top values,
            # plus verified joins and the grain. This agent used to start
            # blind and spend most of its budget calling inspect_schema and
            # inspect_column to rediscover facts already sitting in memory;
            # each of those round trips resends the whole growing
            # conversation, so ~500 tokens of up-front facts replaces several
            # thousand. Tools remain for what the map genuinely can't answer:
            # whole rows read together, a full value list beyond the top 8.
            data_map_block = (
                structural.data_map.to_prompt()
                if structural.data_map is not None
                else "(no data map available - use the tools to inspect the schema)"
            )
            agent = create_agent(
                model=model,
                tools=tools,
                system_prompt=(
                    f"{CONTEXT_DISCOVERY_SYSTEM_PROMPT}"
                    f"\n\nCANDIDATE INDUSTRIES:\n{_industries_block(packs)}"
                    f"\n\nWHAT PROFILING ALREADY MEASURED (treat as established fact; "
                    f"do not spend a tool call re-checking any of it):\n{data_map_block}"
                ),
            )

            agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "The profile above already tells you the schema, every column's "
                                "cardinality and top values, and the verified joins. Use tools ONLY "
                                "if something is genuinely undecidable from it - for example reading "
                                "whole rows together to see how columns relate. Then call "
                                "submit_context_findings once."
                            ),
                        }
                    ]
                },
                config={"recursion_limit": MAX_AGENT_STEPS, "callbacks": [recorder]},
            )

            # The agent's own claim is the only domain signal accepted here.
            # There is deliberately no name-keyword fallback: guessing a slug
            # from column names is the failure mode this whole agent exists
            # to replace, and a wrong-but-confident domain is worse than an
            # honest None (CLASSIFY still runs its deterministic matcher, for
            # which `domain` is advisory context, not an override).
            guess_slug = discovered_findings.get("domain_guess")
            if guess_slug and guess_slug in {p.slug for p in packs}:
                domain_guess = guess_slug
                domain_confidence = float(discovered_findings.get("confidence") or 0.0)
        except Exception as exc:
            logger.warning("Context discovery agent failed: %s", exc, exc_info=True)
            agent_error = str(exc)
        finally:
            if on_stats is not None:
                on_stats(recorder.summary())

    # 3. Build and return the structured BusinessContext artifact
    return _build_business_context_model(
        data_source=data_source,
        structural=structural,
        evidence=evidence,
        hypotheses=hypotheses,
        questions=questions,
        dq_issues=dq_issues,
        answers=answers,
        domain_guess=domain_guess,
        domain_confidence=domain_confidence,
        agent_findings=discovered_findings,
        agent_error=agent_error,
    )


# Ordered most- to least- worth-asking. The interview is adaptive by impact
# (spec §14): a critical ambiguity is always asked, a medium one only if
# there is room left in the budget.
_IMPACT_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

MAX_CONTEXT_QUESTIONS = 4
"""Kept small on purpose. Spec §13: "Do not ask 20-30 questions at once."
These sit alongside the data-anomaly questions `profiling/quality.py`
already generates, and a review page nobody finishes is worth less than
three questions they actually answer."""


MAX_REVIEW_QUESTIONS = 8
"""Hard ceiling on the whole review page, across every source.

Three generators feed it - data-anomaly questions, `generate_business_
context_questions`, and this agent - each with its own cap and none aware
of the others. On a 26-column upload that summed to thirteen, which is both
past what spec §13 asks for and past what anyone finishes: the owner
answered all thirteen and the value of the last few was ~0."""


def _review_priority(question: DataQuestion) -> tuple[int, int]:
    """Ordering for the budget above: most-worth-asking first.

    Business-context questions outrank anomaly questions because they resolve
    *meaning* (what a row is, which value means success) - answers that
    change the generated plugin - whereas an anomaly question annotates a
    finding that is already reported either way. `general_notes` is the
    open-ended catch-all and is the first thing to drop."""
    if question.id == GENERAL_NOTES_ID:
        return (3, 0)
    if question.id.startswith("ctx:"):
        return (0, 0)
    if question.kind == "business_context":
        return (1, 0)
    return (2, 0)


def _trim_review_to_budget(review: DataReview, budget: int) -> int:
    """Keep the `budget` most valuable questions. Returns how many were cut."""
    if len(review.questions) <= budget:
        return 0
    ordered = sorted(enumerate(review.questions), key=lambda pair: (_review_priority(pair[1]), pair[0]))
    keep = {index for index, _ in ordered[:budget]}
    cut = len(review.questions) - budget
    # Preserve the original display order among survivors.
    review.questions = [q for i, q in enumerate(review.questions) if i in keep]
    return cut


def _question_column(question: BusinessQuestion) -> str | None:
    """The `table.column` a question is about, taken from its own evidence
    (every question built by `_analyze_structural_evidence` sources its
    evidence as f"{table}.{column}"). Used to avoid asking about a column
    the deterministic pass has already raised a question for."""
    for evidence in question.evidence:
        if "." in evidence.source:
            return evidence.source.rsplit(".", 1)[-1]
    return None


def merge_context_questions_into_review(
    review: DataReview,
    context: BusinessContext,
    *,
    max_questions: int = MAX_CONTEXT_QUESTIONS,
) -> int:
    """Surface the agent's open questions on the human review page.

    Without this the Context Discovery Agent's questions are produced,
    persisted, and never shown - the customer is asked the deterministic
    data-anomaly questions only, and every business ambiguity the agent
    found stays unanswered. Routing them through `DataReview` also means the
    answers come back through the existing path (`to_context().notes`), so
    binding, generation and packaging pick them up with no further wiring.

    Mutates `review` in place and returns how many questions were added.
    Idempotent: re-running never duplicates a question, which matters
    because a resumed run re-executes this stage."""
    already_asked = {q.id for q in review.questions}
    # `profiling/quality.py::generate_business_context_questions` ids its
    # questions "biz:{column}". Asking the owner about the same column twice,
    # in two different phrasings, reads as a broken form.
    covered_columns = {q.id[len("biz:") :] for q in review.questions if q.id.startswith("biz:")}

    candidates = sorted(
        context.open_questions,
        key=lambda q: (_IMPACT_ORDER.get(q.impact, 9), q.question_id),
    )

    added = 0
    for question in candidates:
        if added >= max_questions:
            break
        question_id = f"ctx:{question.question_id}"
        if question_id in already_asked:
            continue
        column = _question_column(question)
        if column and column in covered_columns:
            continue

        # A success-definition question is the one whose answer becomes a
        # real value-set binding ("which of these means converted?"), so it
        # gets multi-select chips; anything else with options is a single
        # choice, and the rest stay free text.
        if question.options and question.category == "success_definition":
            answer_type = "multi_choice"
        elif question.options:
            answer_type = "single_choice"
        else:
            answer_type = "free_text"

        review.questions.append(
            DataQuestion(
                id=question_id,
                question=question.question,
                context=question.context,
                kind="business_context",
                answer_type=answer_type,
                choices=list(question.options),
                why_asking=question.why_asking,
            )
        )
        already_asked.add(question_id)
        if column:
            covered_columns.add(column)
        added += 1

    # This is the last generator to contribute, so it owns the page budget.
    cut = _trim_review_to_budget(review, MAX_REVIEW_QUESTIONS)
    if cut:
        logger.info("Trimmed %d low-priority review question(s) to stay within the budget", cut)

    return added


def answers_to_business_answers(answers: dict[str, str]) -> list[BusinessAnswer]:
    """Turn the review page's `{question_id: answer}` map back into the
    agent's own answer type, so a resumed run can re-run discovery with the
    customer's replies incorporated (spec §13's adaptive loop) rather than
    asking the same questions again.

    Only `ctx:`-prefixed ids belong to this agent; the rest are the
    deterministic data-anomaly questions and are left alone."""
    business_answers: list[BusinessAnswer] = []
    for question_id, answer in answers.items():
        if not question_id.startswith("ctx:") or not answer.strip():
            continue
        # The UI joins multi-select chips with ", " - split them back out so
        # a value-set answer arrives as values, not as one pasted string.
        selected = [part.strip() for part in answer.split(",") if part.strip()]
        business_answers.append(
            BusinessAnswer(
                question_id=question_id[len("ctx:") :],
                answer_text=answer,
                selected_options=selected,
            )
        )
    return business_answers


__all__ = [
    "MAX_CONTEXT_QUESTIONS",
    "answers_to_business_answers",
    "merge_context_questions_into_review",
    "run_context_discovery_agent",
]
