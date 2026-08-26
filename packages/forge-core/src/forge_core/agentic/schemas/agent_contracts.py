"""Structured output contracts and evidence schemas for Data2plugin agentic reasoning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    """Auditable atomic observation supporting an agent decision."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "schema",
        "statistics",
        "sample",
        "relationship",
        "value_set",
        "industry_pack",
        "query_result",
        "validation",
    ]
    source: str = Field(description="Originating tool, profile, or check (e.g., 'column_profile', 'sample_rows')")
    observation: str = Field(description="Factual evidence string observed from the data source or schema")


class IndustryCandidateAssessment(BaseModel):
    """An individual industry pack candidate evaluated by the classification agent."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    signature_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""


class IndustryAssessment(BaseModel):
    """Structured output for the Industry Classification Agent."""

    model_config = ConfigDict(extra="forbid")

    selected_industry: str = Field(description="Slug of the selected industry pack")
    confidence: float = Field(ge=0.0, le=1.0, description="Agent semantic confidence score")
    evidence: list[Evidence] = Field(default_factory=list, description="Grounding evidence for this classification")
    alternatives: list[IndustryCandidateAssessment] = Field(
        default_factory=list, description="Runner-up candidate packs"
    )
    needs_human_confirmation: bool = Field(
        default=False, description="True if classification is ambiguous or confidence < 0.90"
    )


class BindingProposal(BaseModel):
    """Structured output for the Schema Binding Agent for a single canonical role."""

    model_config = ConfigDict(extra="forbid")

    canonical_role: str = Field(description="Canonical role requested by the industry pack")
    physical_column: str = Field(description="Physical column mapped in customer dataset")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence justifying this role binding")
    confidence: float = Field(ge=0.0, le=1.0, description="Calculated binding confidence score")
    alternatives: list[str] = Field(default_factory=list, description="Other plausible physical column candidates")
    needs_human_confirmation: bool = Field(
        default=False, description="True if confidence is below authoritative confirmation threshold"
    )


class KPIProposal(BaseModel):
    """Structured output for the KPI Discovery Agent."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique snake_case identifier for this suggested KPI")
    name: str = Field(description="Human-readable business name of the KPI")
    business_question: str = Field(description="The primary business question this KPI answers")
    description: str = Field(description="Detailed business semantics and calculation logic")
    required_roles: list[str] = Field(description="Canonical roles required to compute this metric/KPI")
    aggregation: str = Field(default="SUM", description="Aggregation function: SUM, COUNT, AVG, MIN, MAX")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence justifying domain relevance")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence in business utility")


class ValidationDiagnosis(BaseModel):
    """Structured output for the Validation Diagnostic Agent upon check failures."""

    model_config = ConfigDict(extra="forbid")

    failed_check: str = Field(description="Name of the failing check from the 10-check harness")
    category: Literal[
        "schema",
        "sql_safety",
        "execution",
        "pii",
        "plugin_spec",
        "mcp",
        "hooks",
        "plausibility",
        "semantic",
    ] = Field(description="Taxonomy category of the detected issue")
    root_cause: str = Field(description="Technical root cause identified from logs and DuckDB errors")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence gathered during diagnosis")
    repairable: bool = Field(description="True if the issue can be safely repaired deterministically")
    recommended_action: str = Field(
        description="Prescribed remedy: 'prune_metric' | 'prune_kpi' | 'prune_binding' | 'unrepairable'"
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Diagnosis confidence score")


__all__ = [
    "Evidence",
    "IndustryCandidateAssessment",
    "IndustryAssessment",
    "BindingProposal",
    "KPIProposal",
    "ValidationDiagnosis",
]
