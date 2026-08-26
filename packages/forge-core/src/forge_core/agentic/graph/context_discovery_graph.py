"""LangGraph state graph implementation for Business Context Discovery.

Follows Section 19 of DATA2PLUGIN_CONTEXT_DISCOVERY_AGENT_IMPLEMENTATION.md:
- Deterministic and evidence-backed profiling
- Separation of Confirmed Facts vs Inferred Hypotheses vs Open Questions
- Evidence-based targeted question generation
- Readiness gate evaluation
"""

from __future__ import annotations

import logging
from typing import Any

from forge_core.agentic.schemas.business_context import (
    BusinessAnswer,
    BusinessContext,
    BusinessField,
    BusinessKPI,
    BusinessQuestion,
    DataQualityIssue,
    EntityDefinition,
    Evidence,
    Hypothesis,
    TimeField,
)
from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile
from forge_core.runtime_session import open_session

logger = logging.getLogger("forge_core.agentic.graph.context_discovery_graph")


# A column that repeats, but only a few times each, is the structural
# signature of "many rows describe the same real-world thing" - the
# 5,530-rows/2,260-phone-numbers case from the spec. Selecting on this
# measured ratio rather than on names like "id"/"phone" is what makes the
# grain question work on a dataset whose key column is called `rashi`.
#
# The floor is high on purpose. A low ratio means a handful of values spread
# over many rows, which is a *dimension* (city, tier, status), not an entity
# key - at 0.05 the first version asked "does one row represent one city?"
# and picked a date column as the entity.
_REPEATED_ENTITY_MIN_RATIO = 0.5
_REPEATED_ENTITY_MAX_RATIO = 0.99

# An enum-shaped column: a small, closed set of labels that actually repeat.
#
# Cardinality alone (at 25) swept up customer_name and email_address and
# asked which customer name meant "success". The second half is `cardinality
# < row_count` - a label is a label *because it is reused*, whereas a name or
# an email is distinct on every row. That comparison is row-count-relative by
# construction, unlike a fixed distinct-ratio ceiling, which a column with
# casing variants defeats by looking more unique than it really is (the
# 'Guitar'/'guitar' split inflates its own cardinality out of range).
_ENUM_MAX_CARDINALITY = 8

# Spec §13 ("do not ask 20-30 questions at once") and §14 (rank by expected
# information gain). Without these caps a 17-column table produced twelve
# critical questions, which also meant the readiness gate could never close.
_MAX_ENUM_QUESTIONS_PER_TABLE = 3
_MAX_QUESTIONS_TOTAL = 8
_QUESTION_IMPACT_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# A repeating column that joins to another table's key is strong evidence
# it names the entity these rows belong to - much stronger than uniqueness
# alone, and the reason `orders.customer_id` beats `orders.shipping_city`.
_ENTITY_ROLES_EXCLUDED = frozenset(
    {
        # A date repeating across rows is ordinary, not evidence of an entity;
        # a measure repeating is likewise meaningless here.
        ColumnRole.DATE,
        ColumnRole.DATETIME,
        ColumnRole.NUMERIC,
        ColumnRole.BOOLEAN_FLAG,
    }
)

# Value-level (never name-level) hints that a category is non-production.
# These only ever raise a *question*; nothing is auto-excluded, so a
# false positive costs one question and never silently drops real rows.
_NON_PROD_VALUE_HINTS = ("test", "staging", "dummy", "sample", "demo", "qa", "temp")


def _distinct_values(data_source: DataSource, table: str, column: str, limit: int = 60) -> list[str]:
    """Real DISTINCT values, not `sample_values`. Casing-variant detection and
    the option chips on a customer-facing question both need the true value
    set: a truncated sample can easily contain 'Guitar' but not 'guitar' and
    report a clean column that isn't."""
    physical = next((t.physical_ref for t in data_source.tables if t.name == table), None)
    if physical is None:
        return []
    con = open_session(data_source)
    try:
        rows = con.execute(
            f'SELECT DISTINCT CAST("{column}" AS VARCHAR) AS v '
            f'FROM {physical} WHERE "{column}" IS NOT NULL LIMIT {limit}'
        ).fetchall()
        return [str(r[0]) for r in rows if r[0] is not None]
    except Exception as exc:  # noqa: BLE001 - evidence gathering must never block a run
        logger.warning("Could not read distinct values for %s.%s: %s", table, column, exc)
        return []
    finally:
        con.close()


def _analyze_structural_evidence(
    data_source: DataSource,
    structural: StructuralProfile,
    packs: list[IndustryPack] | None = None,
) -> tuple[list[Evidence], list[Hypothesis], list[BusinessQuestion], list[DataQualityIssue]]:
    """Mines evidence, forms explicit hypotheses, and identifies high-impact gaps.

    Every column selected for investigation is chosen by a *measured*
    property (uniqueness ratio, cardinality, profiled role) rather than by
    its name containing an English word, so this behaves the same on an
    arbitrary customer upload in any industry or language."""
    evidence_list: list[Evidence] = []
    hypotheses: list[Hypothesis] = []
    questions: list[BusinessQuestion] = []
    dq_issues: list[DataQualityIssue] = []

    for t in data_source.tables:
        t_cols = structural.columns_for(t.name)
        evidence_list.append(
            Evidence(
                type="schema",
                source=f"table:{t.name}",
                observation=f"Table '{t.name}' contains {t.row_count} rows across {len(t_cols)} columns",
            )
        )

        # 1. Record grain: is there a column that repeats in a way suggesting
        #    multiple rows per real-world entity?
        joined_columns = {
            r.from_column for r in structural.relationships if r.from_table == t.name
        }
        candidates: list[tuple[bool, float, ColumnProfile, str]] = []

        for col in t_cols:
            if t.row_count <= 0:
                continue
            if col.cardinality == t.row_count:
                evidence_list.append(
                    Evidence(
                        type="statistics",
                        source=f"{t.name}.{col.name}",
                        observation=f"Column '{col.name}' is unique across all {t.row_count} rows (a candidate key)",
                    )
                )
                continue
            # PII columns stay eligible here, unlike the enum questions below.
            # The spec's headline example *is* a PII column ("5,530 records
            # but only 2,260 unique phone numbers"), and a contact number is
            # very often the real identity of the entity. This branch only
            # ever reports the column's name and row/distinct counts - never
            # a value - so it leaks nothing; the enum questions, which put
            # observed values on screen as choices, do exclude PII.
            if col.guessed_role in _ENTITY_ROLES_EXCLUDED:
                continue
            if not (_REPEATED_ENTITY_MIN_RATIO <= col.distinct_ratio <= _REPEATED_ENTITY_MAX_RATIO):
                continue

            obs = (
                f"Column '{col.name}' has {col.cardinality} distinct values across {t.row_count} total rows "
                f"({round(col.distinct_ratio * 100, 1)}% unique)"
            )
            evidence_list.append(
                Evidence(type="statistics", source=f"{t.name}.{col.name}", observation=obs)
            )
            joins = col.name in joined_columns
            if joins:
                obs += f"; it also joins to another table, so those rows share a real {col.name}"
            candidates.append((joins, col.distinct_ratio, col, obs))

        # A join-backed column wins over a merely-repeating one; among equals,
        # the most nearly unique is the best "entity these rows belong to".
        primary_entity_col = None
        repeated_id_obs = ""
        if candidates:
            joins, _ratio, primary_entity_col, repeated_id_obs = max(
                candidates, key=lambda c: (c[0], c[1])
            )

        has_repeated_id = primary_entity_col is not None

        if has_repeated_id and primary_entity_col:
            hypotheses.append(
                Hypothesis(
                    id="hyp_record_grain_interaction",
                    category="record_grain",
                    claim=f"Each row in '{t.name}' represents an individual interaction, event, or attempt rather than a unique {primary_entity_col.name.replace('_id', '').replace('_number', '')}.",
                    confidence=0.85,
                    evidence=[
                        Evidence(
                            type="statistics",
                            source=f"{t.name}.{primary_entity_col.name}",
                            observation=repeated_id_obs,
                        )
                    ],
                )
            )
            questions.append(
                BusinessQuestion(
                    question_id=f"q_grain_{t.name}",
                    category="record_grain",
                    question=(
                        f"In '{t.name}', the same '{primary_entity_col.name}' value appears on several rows. "
                        f"What does a single row actually represent in your business?"
                    ),
                    context=repeated_id_obs,
                    evidence=[
                        Evidence(
                            type="statistics",
                            source=f"{t.name}.{primary_entity_col.name}",
                            observation=repeated_id_obs,
                        )
                    ],
                    # Options are framed around the observed column, not around
                    # a guessed industry vocabulary - the customer supplies the
                    # domain noun via the free-text write-in.
                    options=[
                        f"One row = one separate event or activity (several rows share a '{primary_entity_col.name}')",
                        f"One row = one distinct '{primary_entity_col.name}' (repeats are duplicates to clean up)",
                        f"One row = a point-in-time snapshot of a '{primary_entity_col.name}'",
                    ],
                    impact="critical",
                    why_asking="Clarifying record grain prevents double-counting metrics and ensures conversion rates are calculated per entity vs per interaction accurately.",
                )
            )
        else:
            hypotheses.append(
                Hypothesis(
                    id="hyp_record_grain_entity",
                    category="record_grain",
                    claim=f"Each row in '{t.name}' represents a distinct entity instance.",
                    confidence=0.80,
                    evidence=[
                        Evidence(
                            type="schema",
                            source=f"table:{t.name}",
                            observation=f"Table has {t.row_count} rows without evident repeating primary key patterns.",
                        )
                    ],
                )
            )

        # 2-4. Enum-shaped columns drive the remaining analyses. Selected by
        #      measured cardinality, not by the name matching "status" or
        #      "outcome" - a column called `sonuc` or `estado` is an enum on
        #      exactly the same evidence, and a column called `status_report`
        #      holding 4,000 distinct free-text values is correctly not one.
        # Ranked smallest-first: a genuine status field is a short closed set,
        # so low cardinality is the best structural proxy for "this records an
        # outcome" available without reading the column's name. Capped per
        # table so a wide table can't bury the owner in near-identical
        # questions - the LLM pass is what picks the *meaningful* one when a
        # key is configured; this deterministic floor just makes sure the
        # highest-signal candidates get asked.
        enum_candidates = sorted(
            (
                c
                for c in t_cols
                if c.guessed_role in (ColumnRole.CATEGORICAL, ColumnRole.BOOLEAN_FLAG)
                and 1 < c.cardinality <= _ENUM_MAX_CARDINALITY
                and c.cardinality < t.row_count
                and not c.is_likely_pii
                and not c.is_likely_identifier
            ),
            key=lambda c: (c.cardinality, c.name),
        )
        # Only the *questions* are capped. Data-quality detection runs over
        # every candidate: finding "Guitar" and "guitar" in the same column
        # is cheap, always worth reporting, and must not be silently skipped
        # just because two other columns sorted ahead of it.
        for enum_rank, col in enumerate(enum_candidates):
            asks_outcome_question = enum_rank < _MAX_ENUM_QUESTIONS_PER_TABLE
            values = _distinct_values(data_source, t.name, col.name)
            if not values:
                continue
            shown = ", ".join(values[:8])
            evidence_list.append(
                Evidence(
                    type="value_set",
                    source=f"{t.name}.{col.name}",
                    observation=f"Column '{col.name}' holds {col.cardinality} distinct values: {shown}",
                )
            )

            # 2. Which of these values means "this went well"? Only the
            #    customer knows - the spec is explicit that labels like
            #    "favorable"/"closed" must not be interpreted for them.
            hypotheses.append(
                Hypothesis(
                    id=f"hyp_enum_{t.name}_{col.name}",
                    category="field_semantics",
                    claim=(
                        f"Column '{col.name}' is a categorical state/label field with "
                        f"{col.cardinality} distinct values, usable for segmenting metrics."
                    ),
                    confidence=0.8,
                    evidence=[
                        Evidence(
                            type="value_set",
                            source=f"{t.name}.{col.name}",
                            observation=f"Distinct values: {shown}",
                        )
                    ],
                )
            )
            if asks_outcome_question:
                questions.append(
                    BusinessQuestion(
                        question_id=f"q_outcome_{col.name}",
                        category="success_definition",
                        question=(
                            f"In '{col.name}', which of these values means the outcome was a success "
                            f"for your business? (Choose any that apply, or describe it in your own words.)"
                        ),
                        context=f"Observed values in dataset include: {shown}",
                        evidence=[
                            Evidence(
                                type="value_set",
                                source=f"{t.name}.{col.name}",
                                observation=f"Observed values: {shown}",
                            )
                        ],
                        options=values[:6],
                        # Only the single best candidate per table is critical.
                        # Marking every enum critical meant the readiness gate
                        # (which requires all critical questions resolved) could
                        # never close on a wide table.
                        impact="critical" if enum_rank == 0 else "medium",
                        why_asking="Knowing which values count as success enables accurate conversion rates, funnel dashboards, and revenue KPIs.",
                    )
                )

            # 3. Non-production values, detected in the data itself.
            test_candidates = [
                v for v in values if any(hint in v.lower() for hint in _NON_PROD_VALUE_HINTS)
            ]
            if test_candidates:
                dq_issues.append(
                    DataQualityIssue(
                        code="TEST_ENVIRONMENT_VALUES_PRESENT",
                        severity="medium",
                        table=t.name,
                        column=col.name,
                        summary=f"Column '{col.name}' contains possible non-production values: {', '.join(test_candidates)}",
                        business_impact="May skew real customer conversion and revenue metrics if included in production reports.",
                        suggested_handling="Confirm with the data owner, then exclude in filter predicates.",
                    )
                )
                questions.append(
                    BusinessQuestion(
                        question_id=f"q_filter_{col.name}",
                        category="data_quality",
                        question=f"Should any of these '{col.name}' values be left out of your reports?",
                        context=f"These look like they might be test or internal records: {', '.join(test_candidates)}",
                        evidence=[
                            Evidence(
                                type="data_quality",
                                source=f"{t.name}.{col.name}",
                                observation=f"Found candidate non-production values: {', '.join(test_candidates)}",
                            )
                        ],
                        options=test_candidates,
                        impact="high",
                        why_asking="Excluding test records ensures your KPIs reflect genuine customer activity.",
                    )
                )

            # 4. Casing/whitespace variants that would split a GROUP BY.
            lower_map: dict[str, list[str]] = {}
            for v in values:
                lower_map.setdefault(v.strip().lower(), []).append(v)
            flat_variants = [v for variants in lower_map.values() if len(variants) > 1 for v in variants]
            if flat_variants:
                dq_issues.append(
                    DataQualityIssue(
                        code="CATEGORY_CASING_VARIATIONS",
                        severity="medium",
                        table=t.name,
                        column=col.name,
                        summary=f"Column '{col.name}' has casing variations (e.g. {flat_variants[:4]})",
                        business_impact="May cause split rows in group-by reporting (e.g. 'guitar' vs 'Guitar').",
                        suggested_handling="Normalize with LOWER(TRIM(col)) in metric compilation.",
                    )
                )
                questions.append(
                    BusinessQuestion(
                        question_id=f"q_casing_{col.name}",
                        category="field_semantics",
                        question=f"In '{col.name}', should {' and '.join(flat_variants[:2])} count as the same thing?",
                        context=f"The same value appears with different capitalisation: {flat_variants[:6]}",
                        evidence=[
                            Evidence(
                                type="data_quality",
                                source=f"{t.name}.{col.name}",
                                observation=f"Casing variations detected: {flat_variants[:6]}",
                            )
                        ],
                        options=["Yes, treat them as one", "No, they are genuinely different"],
                        impact="medium",
                        why_asking="Ensures category breakdowns and leaderboards are clean without duplicate segmented rows.",
                    )
                )

    # Spec §14: ask the highest-expected-information-gain questions first,
    # and stop. Sorted stably by impact so the cap drops the least useful
    # questions rather than whichever table happened to be profiled last.
    questions.sort(key=lambda q: _QUESTION_IMPACT_ORDER.get(q.impact, 9))
    return evidence_list, hypotheses, questions[:_MAX_QUESTIONS_TOTAL], dq_issues


def _build_business_context_model(
    data_source: DataSource,
    structural: StructuralProfile,
    evidence: list[Evidence],
    hypotheses: list[Hypothesis],
    questions: list[BusinessQuestion],
    dq_issues: list[DataQualityIssue],
    answers: list[BusinessAnswer] | None = None,
    domain_guess: str | None = None,
    domain_confidence: float = 0.0,
    agent_findings: dict[str, Any] | None = None,
    agent_error: str | None = None,
) -> BusinessContext:
    """Constructs the authoritative BusinessContext model.

    Everything here is either observed, agent-claimed, or user-confirmed.
    Fields with no such backing stay None/empty rather than being filled with
    a plausible-sounding default: a fabricated business process or KPI reads
    exactly like a discovered one downstream, and the whole point of this
    agent is that downstream stages can trust what it hands them."""
    answers = answers or []
    answer_map = {a.question_id: a for a in answers}
    agent_findings = agent_findings or {}

    # Record confirmed facts from answers
    confirmed_facts: list[Evidence] = []
    for a in answers:
        confirmed_facts.append(
            Evidence(
                type="customer_confirmation",
                source=f"answer:{a.question_id}",
                observation=f"User responded: {a.answer_text} (Selections: {', '.join(a.selected_options)})",
            )
        )

    # Primary entities. The identifier comes from the structural profile's
    # measured uniqueness (profiling/structural.py already decides this from
    # real cardinality, not from the name containing "id"), so a dataset
    # whose key column is called `rashi` or `folio` is handled identically.
    primary_entities = []
    for t in data_source.tables:
        t_cols = structural.columns_for(t.name)
        id_col = next((c for c in t_cols if c.guessed_role == ColumnRole.IDENTIFIER), None)
        if id_col is None:
            # No measured key: fall back to the least-repeating column so the
            # entity still names something real, and flag it as non-unique.
            id_col = max(t_cols, key=lambda c: c.distinct_ratio, default=None)
        if id_col is None:
            continue
        primary_entities.append(
            EntityDefinition(
                name=t.name.replace("_", " ").title(),
                table=t.name,
                identifier_column=id_col.name,
                is_unique_key=id_col.cardinality == t.row_count and t.row_count > 0,
                description=f"Core entity representing records in {t.name}",
            )
        )

    # Important Dimensions and Measures
    dimensions: list[BusinessField] = []
    measures: list[BusinessField] = []
    time_semantics: list[TimeField] = []

    # Roles come from the structural profile, which derives them from real
    # values (dtype, measured uniqueness, TRY_CAST date parseability) rather
    # than from name substrings - see profiling/structural.py::_guess_role.
    for col in structural.columns:
        if col.guessed_role == ColumnRole.DATE:
            time_semantics.append(
                TimeField(
                    table=col.table,
                    column=col.name,
                    # Which date this *is* (event vs created vs scheduled) is
                    # a business fact nothing here has observed; default to
                    # the neutral option and let a customer answer refine it.
                    date_type="event_date",
                    description=f"Temporal column {col.name}",
                )
            )
        elif col.guessed_role == ColumnRole.NUMERIC and not col.is_likely_identifier:
            measures.append(
                BusinessField(
                    table=col.table,
                    column=col.name,
                    business_name=col.name.replace("_", " ").title(),
                    role_type="measure",
                    description=f"Numeric column {col.name}",
                    # Whether this column can be summed is a semantic claim
                    # that must be gate-verified (see validation/gates.py and
                    # compiler/metric_generator.py's default-deny SUM); being
                    # numeric is not evidence of additivity.
                    is_additive=False,
                )
            )
        elif col.guessed_role != ColumnRole.IDENTIFIER:
            dimensions.append(
                BusinessField(
                    table=col.table,
                    column=col.name,
                    business_name=col.name.replace("_", " ").title(),
                    role_type="dimension",
                    description=f"Categorical column {col.name}",
                )
            )

    # Candidate KPIs are deliberately NOT invented here. A KPI the customer
    # never asked for, phrased as though it were discovered, is exactly the
    # "pretend an inference is confirmed fact" failure the spec forbids -
    # and the real KPI catalog is compiled downstream from the industry pack
    # against verified bindings, which is the path that can actually prove a
    # metric runs. Left empty until a user answer or the agent supplies one.
    candidate_kpis: list[BusinessKPI] = []

    # Open questions: filter out answered questions
    open_questions = [q for q in questions if q.question_id not in answer_map]

    # Readiness gate (spec §21): every critical ambiguity must be resolved,
    # and a failed agent pass can never read as "ready" - an exception in the
    # semantic layer means we know strictly less, not more.
    critical_unanswered = [q for q in open_questions if q.impact == "critical"]
    ready = not critical_unanswered and agent_error is None

    # Confidence tracks how much of what we asked actually got answered,
    # rather than snapping between two hardcoded constants.
    asked = len(questions)
    resolved = sum(1 for q in questions if q.question_id in answer_map)
    coverage = 1.0 if asked == 0 else resolved / asked
    overall_confidence = round(min(0.95, 0.55 + 0.40 * coverage), 3)
    if agent_error is not None:
        overall_confidence = round(overall_confidence * 0.5, 3)

    # Grain: agent-claimed if it investigated, else the structural
    # hypothesis, else unknown. Never a guess dressed up as a finding.
    record_grain = agent_findings.get("record_grain") or None
    if not record_grain:
        grain_hyp = next((h for h in hypotheses if h.category == "record_grain"), None)
        record_grain = grain_hyp.claim if grain_hyp else None

    return BusinessContext(
        domain=domain_guess,
        domain_confidence=domain_confidence,
        # Only the customer can state their objective; until they answer,
        # this stays unknown rather than asserting a generic one.
        business_objective=None,
        dataset_purpose=agent_findings.get("reasoning") or None,
        record_grain=record_grain,
        primary_entities=primary_entities,
        relationships=[],
        # A lifecycle must be observed (ordered status transitions) or
        # confirmed by the customer. Neither has happened yet.
        business_process=None,
        important_dimensions=dimensions[:10],
        important_measures=measures[:5],
        time_semantics=time_semantics,
        status_definitions=[],
        # Which values mean "converted" is precisely what the critical
        # success_definition question asks; asserting an answer here would
        # make that question pointless.
        success_definition=None,
        candidate_kpis=candidate_kpis,
        desired_questions=[],
        data_quality_issues=dq_issues,
        confirmed_facts=confirmed_facts,
        inferred_hypotheses=hypotheses,
        open_questions=open_questions,
        overall_confidence=overall_confidence,
        ready_for_downstream_pipeline=ready,
    )


__all__ = [
    "_analyze_structural_evidence",
    "_build_business_context_model",
]
