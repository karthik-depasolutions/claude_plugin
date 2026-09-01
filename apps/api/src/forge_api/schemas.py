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
    use_agent: bool = Field(
        False,
        description="Use a tool-using LangChain agent (schema inspection, live data preview, "
        "web search) to resolve schema roles the deterministic scorer and single-shot LLM "
        "proposer both fail on, instead of leaving them unresolved.",
    )
    label: str | None = Field(
        None,
        description="Optional project/business name (e.g. 'Sparda Music Academy') - personalizes "
        "the packaged plugin's displayName/name and, for an upload loaded into the client "
        "warehouse, the Postgres schema name. Defaults to a generic '<pack>-mis-plugin' name.",
    )


class TokenUsageOut(BaseModel):
    """LLM tokens spent generating a run (forge_core.llm.TokenUsage.snapshot)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    by_model: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_role: dict[str, dict[str, int]] = Field(default_factory=dict)


class RunSummary(BaseModel):
    run_id: str
    status: str
    current_stage: str | None
    error: str | None
    total_tokens: int = 0


class RunDetail(RunRecord):
    """`RunRecord` plus what the API layer tracks that the core model doesn't
    — currently the LLM token usage, kept off `RunRecord` because it's an API
    concern (persisted on the `runs` row, not in the pipeline's own state)."""

    token_usage: TokenUsageOut | None = None


class ConfirmIndustryRequest(BaseModel):
    industry: str = Field(..., description="Pack slug chosen from the classify stage's ranked_matches.")


class DataAnswersRequest(BaseModel):
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="question id -> the owner's answer. An empty object means 'I have nothing "
        "to add' and lets the run proceed without any clarifications.",
    )


class BindingOverridesRequest(BaseModel):
    overrides: dict[str, str] = Field(
        ..., description="canonical_role -> physical 'table.column', forcing the deterministic guess."
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
