"""Industry Pack contract — the moat asset described in the architecture doc
(section 4.4 / 15). A pack is authored once per industry and reused across
every customer in that industry via the binding layer, never hand-edited
per customer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VocabularyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    definition: str


class RequiredRoles(BaseModel):
    """What a KPI needs, expressed in canonical-role terms — never physical
    column names. This is what the binding resolver must satisfy before a
    KPI can be compiled for a given customer."""

    model_config = ConfigDict(extra="forbid")

    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)


class CanonicalKpi(BaseModel):
    """A KPI defined once, in canonical-role terms, industry-wide.

    `sql` is a Jinja-style template over canonical role names and the
    industry-wide table aliases declared in `IndustryPack.table_aliases`
    (e.g. `{{fact}}`, `{{revenue_amount}}`, `{{completed_values}}`). It is
    never executed directly — `forge_core.compiler.kpi_compiler` renders it
    against a customer's `SchemaBindings` into concrete, validated SQL.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    description: str
    formula_plain_english: str
    grain: str
    requires: RequiredRoles
    sql: str
    unit: str = "count"
    assertions: list[str] = Field(
        default_factory=list,
        description="Python expressions over the KPI's own result columns, "
        "evaluated during the dry-run validation check, e.g. 'total_revenue >= 0'.",
    )
    optional: bool = Field(
        default=False, description="If true, absence of required roles is a warn, not a fail."
    )

    @field_validator("assertions")
    @classmethod
    def _assertions_are_safe(cls, v: list[str]) -> list[str]:
        # Imported lazily: forge_core.validation pulls in the whole harness
        # (generation -> models), which would be a module-load cycle here.
        from forge_core.validation.assertion_policy import validate_assertion

        for expr in v:
            validate_assertion(expr)
        return v


class Guardrails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    denied_role_categories: list[str] = Field(
        default_factory=lambda: ["pii_direct_identifier"],
        description="Canonical role categories that must never appear in a KPI "
        "projection, artifact, or generated example (e.g. name, phone, email).",
    )
    require_read_only: bool = True
    max_query_rows: int = 200
    query_timeout_seconds: int = 10
    notes: list[str] = Field(default_factory=list)


class PackSignature(BaseModel):
    """Deterministic matching fingerprint used by the classifier — see
    forge_core.classification.matcher. Never LLM-generated; hand-curated per
    pack and versioned alongside it."""

    model_config = ConfigDict(extra="forbid")

    entity_name_hints: list[str] = Field(default_factory=list)
    column_name_hints: list[str] = Field(default_factory=list)
    required_role_categories: list[str] = Field(default_factory=list)
    table_count_range: tuple[int, int] = (1, 50)


class TemplateRef(BaseModel):
    """Pointer to a Jinja template file inside the pack directory, plus the
    metadata the generator needs before rendering (which tools it may cite,
    what canonical roles must be resolved for it to be relevant)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    template_path: str
    requires_roles: list[str] = Field(default_factory=list)
    description: str = ""


class IndustryPack(BaseModel):
    """The complete pack, loaded from `industry-packs/<slug>/pack.json` plus
    its `kpis/*.json` sibling files."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    version: str
    description: str
    vocabulary: list[VocabularyEntry] = Field(default_factory=list)
    canonical_roles: dict[str, str] = Field(
        default_factory=dict,
        description="canonical_role -> human description, e.g. "
        "'revenue_amount': 'the monetary amount of a completed transaction'.",
    )
    role_hints: dict[str, list[str]] = Field(
        default_factory=dict,
        description="canonical_role -> lowercase column-name substrings the deterministic binder "
        "should treat as a strong signal, e.g. 'product_name': ['package', 'product', 'item']. "
        "Only needed when generic token overlap can't disambiguate synonyms.",
    )
    table_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Canonical table alias -> description, e.g. 'fact': 'the primary transaction table'.",
    )
    kpis: list[CanonicalKpi] = Field(default_factory=list)
    value_set_hints: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Logical value-set name (referenced as {{name}} in KPI SQL, usually inside an "
        "IN clause) -> lowercase substring hints used to match it against a customer's actual "
        "category values, e.g. 'completed_values': ['completed', 'delivered', 'paid'].",
    )
    skill_templates: list[TemplateRef] = Field(default_factory=list)
    agent_templates: list[TemplateRef] = Field(default_factory=list)
    recipe_templates: list[TemplateRef] = Field(default_factory=list)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    signature: PackSignature = Field(default_factory=PackSignature)

    def kpi(self, kpi_id: str) -> CanonicalKpi:
        for k in self.kpis:
            if k.id == kpi_id:
                return k
        raise KeyError(f"No KPI {kpi_id!r} in pack {self.slug!r}")


class IndustryMatch(BaseModel):
    """One ranked classification result — Stage 3 output entry."""

    model_config = ConfigDict(extra="forbid")

    pack_slug: str
    confidence: float = Field(ge=0.0, le=1.0)
    matched_signals: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked_matches: list[IndustryMatch]
    primary_pack_slug: str
    secondary_pack_slug: str | None = Field(
        default=None, description="Set when the platform supports a blended-industry pack."
    )
    requires_customer_confirmation: bool = Field(
        default=False, description="True when top confidence is below the auto-accept threshold."
    )
