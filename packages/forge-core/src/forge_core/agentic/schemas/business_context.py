"""Business Context schemas for the Context Discovery Agent.

Provides strongly-typed Pydantic v2 models for:
- Auditable atomic Evidence
- Inferred Hypotheses vs Confirmed Facts
- Business Questions with context and choices
- Entity, Relationship, Lifecycle, and Metric definitions
- Authoritative BusinessContext container
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    """Auditable atomic observation supporting a hypothesis or decision."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "schema",
        "statistics",
        "sample",
        "relationship",
        "value_set",
        "query_result",
        "industry_pack",
        "validation",
        "customer_confirmation",
        "data_quality",
    ]
    source: str = Field(description="Originating tool, table, column, or check")
    observation: str = Field(description="Factual evidence string observed from data or schema")


class Hypothesis(BaseModel):
    """An explicit data or business interpretation grounded in evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique snake_case identifier for this hypothesis")
    category: Literal[
        "record_grain",
        "entity_identity",
        "business_process",
        "business_objective",
        "success_definition",
        "field_semantics",
        "time_semantics",
        "kpi",
        "data_quality",
    ]
    claim: str = Field(description="The inferred statement or claim about the business data")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0, description="Confidence in this hypothesis (0.0 to 1.0)")
    evidence: list[Evidence] = Field(default_factory=list, description="List of supporting evidence items")
    status: Literal["proposed", "confirmed", "rejected", "superseded"] = Field(
        default="proposed", description="Current lifecycle state of the hypothesis"
    )


class BusinessQuestion(BaseModel):
    """A targeted clarification question for the customer to resolve business ambiguity."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(description="Unique snake_case identifier for the question")
    category: Literal[
        "record_grain",
        "entity_identity",
        "business_process",
        "business_objective",
        "success_definition",
        "field_semantics",
        "time_semantics",
        "kpi",
        "data_quality",
        "other",
    ]
    question: str = Field(description="Customer-facing, non-technical question text")
    context: str = Field(description="Observed data patterns or evidence explaining why this is asked")
    evidence: list[Evidence] = Field(default_factory=list, description="Grounding evidence for the question")
    options: list[str] = Field(default_factory=list, description="Suggested candidate choices/pills if applicable")
    required: bool = Field(default=True, description="Whether this question blocks readiness")
    impact: Literal["low", "medium", "high", "critical"] = Field(
        default="high", description="Potential impact on downstream plugin quality and KPIs"
    )
    why_asking: str = Field(default="", description="Brief explanation of how the answer helps build the plugin")


class BusinessAnswer(BaseModel):
    """A customer-supplied response to a BusinessQuestion."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    answer_text: str
    selected_options: list[str] = Field(default_factory=list)


class EntityDefinition(BaseModel):
    """Definition of a core business entity identified in the data."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Entity name (e.g. 'Lead', 'Student', 'Order', 'Course')")
    table: str = Field(description="Primary backing table name")
    identifier_column: str = Field(description="Primary identifying column")
    is_unique_key: bool = Field(default=True, description="Whether the identifier is globally unique in table")
    description: str = Field(default="", description="Business role of this entity")


class RelationshipDefinition(BaseModel):
    """A discovered or confirmed relationship between entities or tables."""

    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_type: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    description: str = ""


class LifecycleEvent(BaseModel):
    """An event in the business process lifecycle."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Event name (e.g. 'Created', 'Contacted', 'Trial Scheduled', 'Enrolled')")
    stage_order: int = Field(default=1, description="Sequential stage order (1-indexed)")
    trigger_column: str | None = None
    trigger_values: list[str] = Field(default_factory=list)
    description: str = ""


class BusinessProcessDefinition(BaseModel):
    """Overall workflow or lifecycle model discovered from the dataset."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Name of the process (e.g. 'Student Trial Admissions Funnel')")
    description: str = ""
    stages: list[LifecycleEvent] = Field(default_factory=list)


class BusinessField(BaseModel):
    """An important business dimension or measure."""

    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    business_name: str
    role_type: Literal["dimension", "measure", "identifier", "timestamp", "status"]
    description: str = ""
    unit: str | None = None
    is_additive: bool = False


class TimeField(BaseModel):
    """Time-related semantics for reporting."""

    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    date_type: Literal["event_date", "created_date", "updated_date", "scheduled_date", "expiry_date"]
    timezone_hint: str | None = None
    description: str = ""


class StatusDefinition(BaseModel):
    """Categorical status field mapping."""

    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    active_values: list[str] = Field(default_factory=list)
    terminal_success_values: list[str] = Field(default_factory=list)
    terminal_failure_values: list[str] = Field(default_factory=list)
    excluded_values: list[str] = Field(default_factory=list)


class SuccessDefinition(BaseModel):
    """Explicit definition of what counts as a positive business conversion."""

    model_config = ConfigDict(extra="forbid")

    conversion_event: str = Field(description="Name of conversion event (e.g. 'Trial Attended', 'Enrolled')")
    criteria: str = Field(description="Exact criteria or column condition for conversion")
    qualifying_columns: list[str] = Field(default_factory=list)
    qualifying_values: list[str] = Field(default_factory=list)


class BusinessKPI(BaseModel):
    """A proposed high-impact business KPI."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique snake_case KPI ID")
    name: str = Field(description="Human-readable business name")
    business_question: str = Field(description="The primary business question this KPI answers")
    formula_description: str = Field(description="Plain English calculation logic")
    aggregation: str = "COUNT"
    required_columns: list[str] = Field(default_factory=list)
    target_direction: Literal["higher_is_better", "lower_is_better", "target_range"] = "higher_is_better"


class DataQualityIssue(BaseModel):
    """A detected data quality issue with business impact analysis."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["low", "medium", "high", "critical"]
    table: str
    column: str
    summary: str
    business_impact: str = ""
    suggested_handling: str = ""


class SecurityConsideration(BaseModel):
    """A security, privacy, or PII consideration."""

    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    classification: Literal["pii", "phi", "financial", "internal", "public"]
    reason: str


class BusinessContext(BaseModel):
    """The authoritative, structured business-context artifact produced by the Discovery Agent."""

    model_config = ConfigDict(extra="forbid")

    domain: str | None = Field(default=None, description="Inferred or confirmed industry domain slug")
    domain_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    business_objective: str | None = Field(default=None, description="Primary business goal (e.g. 'Lead conversion optimization')")
    dataset_purpose: str | None = Field(default=None, description="Summary of what the dataset records")

    record_grain: str | None = Field(default=None, description="What one physical row represents (e.g. 'one lead interaction')")

    primary_entities: list[EntityDefinition] = Field(default_factory=list)
    relationships: list[RelationshipDefinition] = Field(default_factory=list)

    business_process: BusinessProcessDefinition | None = None
    lifecycle_events: list[LifecycleEvent] = Field(default_factory=list)

    important_dimensions: list[BusinessField] = Field(default_factory=list)
    important_measures: list[BusinessField] = Field(default_factory=list)
    time_semantics: list[TimeField] = Field(default_factory=list)

    status_definitions: list[StatusDefinition] = Field(default_factory=list)
    success_definition: SuccessDefinition | None = None

    candidate_kpis: list[BusinessKPI] = Field(default_factory=list)
    desired_questions: list[str] = Field(default_factory=list)

    data_quality_issues: list[DataQualityIssue] = Field(default_factory=list)

    confirmed_facts: list[Evidence] = Field(default_factory=list)
    inferred_hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[BusinessQuestion] = Field(default_factory=list)

    security_considerations: list[SecurityConsideration] = Field(default_factory=list)

    overall_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ready_for_downstream_pipeline: bool = Field(
        default=False, description="True only when core grain, entities, and critical semantics are sufficiently resolved"
    )

    def to_handoff(self) -> dict[str, Any]:
        """The §22 handoff contract: what downstream stages consume so they
        don't re-derive business context for themselves.

        Merged into the run's shared `data_context` payload (see
        `DataReview.to_context`), which binding, KPI proposal, generation and
        packaging already read - so one merge reaches every consumer instead
        of threading a new argument through four signatures.

        Confirmed facts and hypotheses are kept in **separate** keys on
        purpose. Collapsing them is the "pretend an inference is confirmed
        fact" failure the spec forbids: a prompt that cannot tell "the owner
        told us X" from "we suspect X" will treat both as settled."""
        entities = [
            {
                "name": e.name,
                "table": e.table,
                "identifier_column": e.identifier_column,
                "is_unique_key": e.is_unique_key,
            }
            for e in self.primary_entities
        ]
        payload: dict[str, Any] = {
            "domain": self.domain,
            "domain_confidence": self.domain_confidence,
            "record_grain": self.record_grain,
            "primary_entities": entities,
            "confirmed_facts": [
                {"source": e.source, "observation": e.observation} for e in self.confirmed_facts
            ],
            "hypotheses": [
                {"claim": h.claim, "category": h.category, "confidence": h.confidence}
                for h in self.inferred_hypotheses
            ],
            "unresolved_questions": [
                {"question": q.question, "category": q.category, "impact": q.impact}
                for q in self.open_questions
            ],
            "data_quality_issues": [
                {
                    "table": i.table,
                    "column": i.column,
                    "severity": i.severity,
                    "summary": i.summary,
                    "business_impact": i.business_impact,
                }
                for i in self.data_quality_issues
            ],
            "overall_confidence": self.overall_confidence,
            "ready_for_downstream_pipeline": self.ready_for_downstream_pipeline,
        }
        if self.business_objective:
            payload["business_objective"] = self.business_objective
        if self.success_definition is not None:
            payload["success_definition"] = {
                "conversion_event": self.success_definition.conversion_event,
                "criteria": self.success_definition.criteria,
                "qualifying_columns": list(self.success_definition.qualifying_columns),
                "qualifying_values": list(self.success_definition.qualifying_values),
            }
        return payload


__all__ = [
    "Evidence",
    "Hypothesis",
    "BusinessQuestion",
    "BusinessAnswer",
    "EntityDefinition",
    "RelationshipDefinition",
    "LifecycleEvent",
    "BusinessProcessDefinition",
    "BusinessField",
    "TimeField",
    "StatusDefinition",
    "SuccessDefinition",
    "BusinessKPI",
    "DataQualityIssue",
    "SecurityConsideration",
    "BusinessContext",
]
