"""Request/response contracts for the HTTP API. Deliberately thin wrappers
around `forge_core.models.run.RunRecord` — the API never invents its own
notion of run state."""

from __future__ import annotations

from forge_core.models.run import RunRecord
from pydantic import BaseModel, Field


class CreateRunFromPathRequest(BaseModel):
    source_path: str = Field(
        ...,
        description=(
            "A path readable by the API process (dev/CLI-parity use case), or a "
            "postgresql:// connection string for a live database. Never echoed back "
            "or persisted verbatim if it's a connection string - see "
            "forge_core.ingestion.registry.prepare_source_for_persistence."
        ),
    )
    industry: str | None = Field(None, description="Force a pack slug, skipping auto-classification.")
    use_llm: bool = Field(True, description="Use Gemini for semantic profiling, generation, and critique.")
    use_agent: bool = Field(
        True,
        description="Use a tool-using LangChain agent (schema inspection, live data preview, "
        "web search) to ground every column's meaning in real values - the default for a real "
        "run. False is the deterministic-only path, kept for offline/CI use.",
    )
    label: str | None = Field(
        None,
        description="Optional project/business name (e.g. 'Sparda Music Academy') - personalizes "
        "the packaged plugin's displayName/name and, for an upload loaded into the client "
        "warehouse, the Postgres schema name. Defaults to a generic '<pack>-mis-plugin' name.",
    )


class RunSummary(BaseModel):
    run_id: str
    status: str
    current_stage: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    label: str | None = None
    industry: str | None = None
    tables: list[str] = Field(default_factory=list)
    kpis_count: int | None = None
    tenant_id: str | None = None
    total_tokens: int = 0
    llm_calls: int = 0


class RunDetail(RunRecord):
    """`RunRecord` plus anything the API layer tracks that the core model
    doesn't (currently nothing extra — kept as a distinct type so API
    consumers have a stable response contract even if that changes)."""


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6, description="Password with at least 6 characters")


class UserPublic(BaseModel):
    email: str
    is_admin: bool = False


class ConfirmIndustryRequest(BaseModel):
    industry: str = Field(..., description="Pack slug chosen from the classify stage's ranked_matches.")


class ReviewRequest(BaseModel):
    industry: str | None = Field(
        None,
        description="Pack slug chosen from the classify stage's ranked_matches. Optional - a run "
        "paused only on data-quality questions can resume without picking an industry.",
    )
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="question id -> answer for the data-quality review's DataQuestions. "
        "Empty dict = reviewed, nothing supplied - which is what tells the orchestrator "
        "not to re-pause on needs_answers.",
    )


class BindingOverridesRequest(BaseModel):
    overrides: dict[str, str] = Field(
        ..., description="canonical_role -> physical 'table.column', forcing the deterministic guess."
    )


class BindingConfirmationRequest(BaseModel):
    confirmations: dict[str, str] = Field(
        default_factory=dict,
        description="canonical_role -> the physical column name to confirm, chosen from that "
        "role's BindingQuestion.physical or one of its .alternatives. Empty dict = reviewed, "
        "every gated binding declined - which is what stops the pause re-firing, exactly like "
        "ReviewRequest.answers={} does for data-quality questions.",
    )


class PackSummary(BaseModel):
    slug: str
    name: str
    version: str
    description: str
    kpi_count: int


class PublishGithubRequest(BaseModel):
    repo_name: str | None = Field(
        None, description="Repo (and marketplace catalog) name. Defaults to the plugin's own name."
    )
    owner: str | None = Field(
        None,
        description="GitHub org/user to create the repo under. Defaults to the GITHUB_ORG env var, "
        "then the GITHUB_TOKEN's own account.",
    )
    private: bool = Field(
        False,
        description="Create the repo as private. Public by default so anyone with the URL can add it as a "
        "Claude Desktop/Code marketplace without first being granted GitHub access to a private repo.",
    )


class PublishGithubResponse(BaseModel):
    repo_full_name: str
    html_url: str
    plugin_name: str
    marketplace_add_command: str
    install_command: str


class WarehouseCredentialsResponse(BaseModel):
    """The one-time-viewable connection string for a run whose data was
    loaded into the client warehouse (forge_core.ingestion.warehouse). Never
    persisted server-side outside process memory - see
    forge_api.registry.RunContext.warehouse_connection_string."""

    connection_string: str = Field(
        ..., description="Set FORGE_SOURCE_DB_URL to this before launching Claude Desktop."
    )
    env_var_name: str = "FORGE_SOURCE_DB_URL"
