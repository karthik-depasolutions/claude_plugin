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
    agent_schema: bool = Field(
        False,
        description="Agent Schema mode for large databases (50-500 tables): read "
        "information_schema to skip O(n²) relationship probing, cap profiling budgets, "
        "chunk LLM synthesis, and ship a sharded, BM25-indexed config/catalog/ the runtime's "
        "search_schema tool reads instead of one monolithic schema_model.json. "
        "Output is unchanged from today when false.",
    )
    no_llm: bool = Field(
        False,
        description="Fast test run: every LLM role is a deterministic stub (no API calls, no "
        "cost), the clarification pause is skipped, and with agent_schema the minimal fast "
        "profiling budget is used. Completes a large database in minutes for $0 - it exercises "
        "the whole pipeline, but the generated plugin has template prose and no verified "
        "cookbook. Not for a production plugin.",
    )
    label: str | None = Field(
        None,
        description="Optional project/business name (e.g. 'Sparda Music Academy') - personalizes "
        "the packaged plugin's displayName/name and, for an upload loaded into the client "
        "warehouse, the Postgres schema name. Defaults to a generic '<pack>-mis-plugin' name.",
    )
    publish_agents_to_source: bool = Field(
        False,
        description="Live-database sources only: after a successful run, also create an "
        "`AGENTS.*` metadata schema (dbt-labs/agents_schema shape) inside the customer's own "
        "source database so their other SQL agents can discover the tables. Best-effort - the "
        "supplied credential must hold CREATE rights; a failure is logged, not fatal. The "
        "generated plugin is unaffected (still read-only, still reads its bundled catalog).",
    )
    clone: bool = Field(
        False,
        description="For a postgresql:// source only: when true, clone a point-in-time copy of the "
        "target schema into an isolated schema on the Forge data warehouse and build the plugin "
        "against that copy, instead of reading the customer's database live. Requires the warehouse "
        "to be configured; a source over the size cap is rejected (HTTP 413).",
    )


class TokenUsageOut(BaseModel):
    """LLM tokens spent generating a run (forge_core.llm.TokenUsage.snapshot)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    estimated_cost_usd: float = 0.0
    by_model: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_role: dict[str, dict[str, int]] = Field(default_factory=dict)


class KnowledgePackOut(BaseModel):
    """How complete the shipped knowledge pack is (schema_model.json). A run on
    a generic dataset with a thin cookbook is still a sellable deliverable, but
    a weaker one - this is what the wizard shows and the harness WARNs on."""

    cookbook_pass_rate: float | None = None
    schema_coverage: float | None = None
    schema_model_cache_hit: bool | None = None


class PackFitOut(BaseModel):
    """Which vertical a run landed on and how much of the pack it could use -
    the demand signal for pack authoring (see models_orm.RunORM)."""

    primary_pack_slug: str | None = None
    pack_confidence: float | None = None
    kpis_compiled: int = 0
    kpis_skipped: int = 0


class RunSummary(BaseModel):
    run_id: str
    status: str
    current_stage: str | None
    error: str | None
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    pack_fit: PackFitOut | None = None


class RunDetail(RunRecord):
    """`RunRecord` plus what the API layer tracks that the core model doesn't
    — LLM token usage, the pack-fit signal and knowledge-pack coverage, kept
    off `RunRecord` because they are API concerns (persisted on the `runs`
    row, not pipeline state)."""

    token_usage: TokenUsageOut | None = None
    pack_fit: PackFitOut | None = None
    knowledge_pack: KnowledgePackOut | None = None
    # The isolated warehouse schema backing this run's data (an upload loaded
    # into the warehouse, or a cloned source database), or null for a live-DB
    # / bundled-file run. A bare identifier, not a credential.
    warehouse_schema: str | None = None


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
