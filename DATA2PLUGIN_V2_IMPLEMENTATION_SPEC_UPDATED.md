# Data2plugin V2 - Production Agentic Architecture & Implementation Specification

## Purpose

This document is an implementation specification for the Data2plugin / MIS Plugin Forge project.

The implementation target is:

- Keep the existing deterministic compiler, security, packaging, MCP runtime, and validation architecture.
- Upgrade orchestration to LangGraph.
- Use LangChain agents only for reasoning-heavy tasks.
- Add durable workflow execution with Temporal for production.
- Use PostgreSQL for durable application state.
- Use object storage for uploaded/generated artifacts.
- Keep DuckDB for analytical execution.
- Keep sqlglot as the SQL AST/parser/validator.
- Add structured agent outputs and evidence-based decisions.
- Add agent tracing/evaluation.
- Do not convert the entire system into an uncontrolled autonomous agent.

The existing project already follows the principle:

> Generate customer-specific configuration; keep MCP execution generic.

It also already has industry packs, deterministic KPI compilation, a generic MCP runtime, a 10-check validation harness, and agentic modules for binding/data understanding. Preserve these foundations.

---


# 0. Product Mission and Problem Statement

## What this project is solving

Data2plugin is not merely a "Claude plugin generator."

The core problem is:

> Businesses have valuable data in heterogeneous sources and formats, but making that data safely understandable and usable through an AI assistant normally requires manual schema analysis, semantic mapping, SQL/metric development, MCP integration, security configuration, testing, and deployment work.

Data2plugin automates that process.

The intended transformation is:

```text
Unknown customer data source
        |
        v
Data2plugin
        |
        +--> understand structure
        +--> understand semantics
        +--> identify business domain
        +--> map customer schema to canonical business roles
        +--> discover useful KPIs/metrics
        +--> apply security and PII policies
        +--> compile deterministic analytics definitions
        +--> generate MCP/plugin configuration
        +--> validate correctness and safety
        |
        v
Customer-specific Claude capability
        |
        v
Natural-language analysis of customer data
```

## One-sentence product definition

> Data2plugin turns a customer's raw business data source into a secure, validated, installable Claude/MCP analytics capability without requiring an engineer to manually build the customer's integration.

## The deeper technical definition

> Data2plugin is an agentic compiler for customer-specific AI data integrations.

The agentic layer handles:

- discovery
- reasoning
- classification
- semantic mapping
- KPI discovery
- validation diagnosis

The deterministic layer handles:

- SQL compilation
- SQL safety
- execution
- PII/security enforcement
- plugin generation
- packaging
- MCP execution

This boundary is mandatory.

## What the user should experience

A customer should be able to:

```text
Upload:
    sales.xlsx

or

Connect:
    PostgreSQL / supported database

        |

        v

Data2plugin understands the data

        |

        v

Data2plugin generates a customer-specific plugin

        |

        v

Customer installs/connects the plugin to Claude

        |

        v

Customer can ask:

"How much revenue did we generate last quarter?"
"Which products are declining?"
"What is our average order value?"
"Which customers purchased more than three times?"
"Show monthly sales."
```

The customer should not need to know LangChain, LangGraph, MCP internals, SQL, schema binding, or agent implementation details.

---

# 0.1 Core Product Success Criterion

The most important product test is:

> Give Data2plugin a previously unseen customer dataset and determine whether it can produce a correct, secure, useful Claude plugin with little or no engineer intervention.

The expected behavior is:

```text
NEW / UNSEEN DATASET
        |
        v
Automatic understanding
        |
        v
Automatic semantic mapping
        |
        v
Automatic KPI/metric discovery
        |
        v
Deterministic compilation
        |
        v
Validation
        |
        +--> genuine ambiguity --> human confirmation
        |
        v
Installable plugin
```

Human involvement is acceptable when the system genuinely lacks enough evidence.

Manual engineering should NOT be required for routine schema mapping, KPI creation, SQL writing, plugin construction, or MCP integration.

## Important distinction

The project is successful when:

```text
new customer data
    ->
working customer-specific AI capability
```

not merely when:

```text
new customer data
    ->
generated files
```

A plugin that is syntactically valid but semantically wrong does not satisfy the product goal.

---

# 0.2 Product Acceptance Test

Create an evaluation suite containing previously unseen datasets from:

```text
retail/e-commerce
finance
healthcare/diagnostics
edtech
generic analytics
```

For each dataset, measure:

```text
1. ingestion success
2. semantic understanding accuracy
3. industry classification accuracy
4. schema binding accuracy
5. KPI semantic correctness
6. SQL compilation success
7. SQL execution success
8. PII/security correctness
9. plugin validation success
10. MCP runtime correctness
11. human intervention rate
12. final customer-question answer correctness
```

The most important end-to-end metric is:

> Can the generated plugin correctly answer representative business questions about the unseen dataset?

---

# 0.3 Product Non-Goals

The product is NOT:

- a generic chatbot over arbitrary files with no semantic model
- a single giant autonomous agent
- a system that blindly generates SQL
- a system that requires every dataset to be embedded into a vector database
- a replacement for deterministic validation
- a replacement for the generic MCP runtime

---

# 1. Target Architecture

```text
React/Vite Web UI
        |
        v
FastAPI API
        |
        v
Temporal Workflow
        |
        v
LangGraph Forge Graph
        |
        +--> deterministic ingestion
        |
        +--> deterministic profiling
        |
        +--> Data Understanding Agent
        |
        +--> Industry Classification Agent
        |
        +--> Schema Binding Agent
        |
        +--> KPI Discovery Agent
        |
        +--> deterministic KPI compiler
        |
        +--> deterministic plugin generator
        |
        +--> deterministic validation harness
        |
        +--> Validation Diagnostic Agent
        |
        +--> human approval when required
        |
        v
Packaged customer-specific plugin
        |
        v
Generic MCP Runtime

Infrastructure:
- PostgreSQL: application metadata/state
- Object Storage: uploaded files + generated ZIPs
- DuckDB: analytical execution
- Temporal: durable workflow execution
- LangGraph: agent workflow/state transitions
- LangSmith: tracing/evaluation
```

## Architectural rule

Agents propose and reason.

Deterministic services decide and execute.

Never let an LLM directly ship executable SQL, security policy, plugin runtime code, or authorization decisions.

---

# 2. Technology Decisions

## 2.1 Python

Use:

- Python 3.11+
- Keep the existing `uv` workspace.

Prefer Python 3.12 if the current project and dependencies support it cleanly.

## 2.2 API

Keep:

- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic

Do not move API responsibilities into the agent framework.

## 2.3 Agent framework

Use:

- `langchain`
- `langgraph`
- provider-specific LangChain integration package for the chosen model

Use LangGraph as the primary orchestration layer.

Use LangChain `create_agent` for individual specialized agents.

Do NOT build a single giant supervisor agent.

## 2.4 Durable workflow

Add:

- `temporalio`

Temporal should own production-grade long-running workflow execution, retries, recovery, and durable human-approval workflows.

Do not use FastAPI `BackgroundTasks` as the durable execution mechanism for production generation jobs.

FastAPI starts/query APIs; Temporal workers execute workflows.

## 2.5 Database

Production:

- PostgreSQL

Keep SQLite only as a local-development/test convenience if useful.

Use SQLAlchemy 2.x async support.

## 2.6 Data/analytics

Keep:

- DuckDB
- `duckdb`
- SQLAlchemy where useful for application DB access

DuckDB is the analytics/exploration engine.

PostgreSQL is the application metadata database.

Do not confuse these responsibilities.

## 2.7 SQL compilation/validation

Keep:

- `sqlglot`

All customer-facing KPI SQL must be produced by deterministic compilation from validated definitions/templates.

Do not allow agents to directly generate executable SQL that bypasses the compiler.

## 2.8 Data validation/schema models

Keep:

- `pydantic>=2`

Use Pydantic models for every agent-to-application boundary.

Avoid parsing free-form agent text with regex.

## 2.9 Object storage

Use an S3-compatible abstraction.

For Azure deployment, prefer:

- Azure Blob Storage

Create a small storage abstraction so the application is not tightly coupled to Azure.

Suggested interface:

```python
class ObjectStorage(Protocol):
    async def put(...)
    async def get(...)
    async def delete(...)
    async def exists(...)
    async def presign(...)
```

## 2.10 Observability/evaluation

Add:

- LangSmith tracing/evaluation for agent workflows.

Keep existing `StageEvent` / run events for product-facing workflow progress.

LangSmith is for engineering observability/evaluation.

The application's own events are for user-facing progress.

## 2.11 MCP

Keep:

- official Python MCP SDK

Keep one generic MCP runtime.

Every generated customer plugin should continue to point to/use the generic runtime plus customer-specific configuration.

---

# 3. Packages to Install

Use `uv`, not ad-hoc pip commands, for this repository.

## Core agent packages

```bash
uv add langchain langgraph
```

## Model provider

The current production implementation may continue using Gemini.

For Gemini:

```bash
uv add langchain-google-genai
```

If additional providers are intentionally supported, add them explicitly, for example:

```bash
uv add langchain-openai langchain-anthropic
```

Do not add provider packages unless the provider is actually supported by the application.

## Temporal

```bash
uv add temporalio
```

## Existing core dependencies to retain

```bash
uv add pydantic duckdb sqlglot jinja2
uv add fastapi "uvicorn[standard]"
uv add sqlalchemy alembic asyncpg
```

For common file ingestion support, retain/add as required by the current implementation:

```bash
uv add pandas pyarrow openpyxl
```

Use DuckDB for SQL-first analytics instead of unnecessarily loading entire datasets into pandas.

## Object storage on Azure

If Azure Blob Storage is the selected deployment target:

```bash
uv add azure-storage-blob azure-identity
```

If storage remains provider-neutral, isolate the SDK behind the storage adapter.

## Optional production Redis

Do not add Redis unless a concrete use case requires it.

If later required for caching/rate-limiting/ephemeral coordination:

```bash
uv add redis
```

Redis is not required for the first V2 implementation.

## Development/test dependencies

```bash
uv add --dev pytest pytest-asyncio pytest-cov ruff mypy
```

If HTTP integration tests use it:

```bash
uv add --dev httpx respx
```

---

# 4. Package Responsibilities

Create/maintain this structure:

```text
packages/
  forge-core/
    src/forge_core/
      domain/
      ingestion/
      profiling/
      classification/
      binding/
      compiler/
      generation/
      packaging/
      validation/
      agentic/
      orchestration/
      security/
      storage/
      evaluation/

  mcp-runtime/
    src/mis_mcp_runtime/

apps/
  api/
  web/

workers/
  temporal_worker/

industry-packs/
fixtures/
generated/
tests/
docs/
```

Do not create an unnecessary monorepo package for every small concept.

---

# 5. New Agentic Package Layout

Recommended:

```text
packages/forge-core/src/forge_core/agentic/

  agents/
    data_understanding.py
    industry_classification.py
    schema_binding.py
    kpi_discovery.py
    validation_diagnosis.py

  graph/
    state.py
    graph.py
    nodes.py
    routing.py
    interrupts.py

  tools/
    schema_tools.py
    profiling_tools.py
    duckdb_tools.py
    relationship_tools.py
    quality_tools.py

  prompts/
    data_understanding.py
    industry_classification.py
    schema_binding.py
    kpi_discovery.py
    validation_diagnosis.py

  schemas/
    understanding.py
    industry.py
    bindings.py
    kpis.py
    diagnosis.py

  memory/
    tenant_memory.py

  model_policy.py
```

The exact filenames may be adapted to the existing repository, but the responsibilities should remain separated.

---

# 6. ForgeGraph State

Create one explicit typed graph state.

Suggested model:

```python
class ForgeState(TypedDict, total=False):
    run_id: str
    tenant_id: str

    datasource_ref: str

    schema_profile: SchemaProfile | None
    data_understanding: DataUnderstanding | None

    industry_candidates: list[IndustryCandidate]
    selected_industry: str | None

    data_review: DataReview | None

    schema_bindings: SchemaBindings | None
    binding_questions: list[BindingQuestion]

    proposed_kpis: list[KPIProposal]
    kpi_defs: list[KpiDef]
    metric_defs: list[MetricDef]

    generated_content: GeneratedContent | None

    validation_report: ValidationReport | None
    validation_diagnosis: ValidationDiagnosis | None

    human_decisions: list[HumanDecision]

    retry_count: int
    status: RunStatus
```

Prefer Pydantic models for domain payloads.

Use TypedDict/dataclass for graph state only where it improves LangGraph integration.

---

# 7. ForgeGraph Nodes

Implement these graph nodes in order:

```text
START
  |
  v
INGEST
  |
  v
PROFILE
  |
  v
UNDERSTAND_DATA
  |
  v
CLASSIFY_INDUSTRY
  |
  +---- low confidence ----> HUMAN_REVIEW
  |
  v
BIND_SCHEMA
  |
  +---- low confidence ----> HUMAN_REVIEW
  |
  v
DISCOVER_KPIS
  |
  v
COMPILE_KPIS
  |
  v
GENERATE_PLUGIN
  |
  v
VALIDATE_PLUGIN
  |
  +---- failed ----> DIAGNOSE_VALIDATION
  |                       |
  |                       v
  |                  permitted repair
  |                       |
  |                       v
  |                  recompile/revalidate
  |
  v
PACKAGE
  |
  v
END
```

Do not make every node an agent.

---

# 8. Data Understanding Agent

Purpose:

Infer the semantic structure of a dataset.

Inputs:

- schema profile
- structural statistics
- column metadata
- safe sample information
- quality findings
- relationship candidates
- industry pack metadata where useful

Tools:

```text
inspect_schema
inspect_table
inspect_column
sample_rows
column_statistics
distinct_values
null_profile
cardinality
detect_candidate_primary_key
detect_candidate_foreign_key
detect_date_columns
detect_measure_columns
detect_dimension_columns
inspect_relationship
run_readonly_duckdb_query
```

The agent must investigate before concluding when evidence is insufficient.

Output must be structured:

```python
class DataUnderstanding(BaseModel):
    domain: str
    tables: list[TableUnderstanding]
    entities: list[Entity]
    relationships: list[Relationship]
    grains: list[TableGrain]
    dimensions: list[ColumnRole]
    measures: list[ColumnRole]
    time_columns: list[TimeColumn]
    findings: list[DataFinding]
    hypotheses: list[Hypothesis]
    confidence: float
```

Never rely on prose parsing.

---

# 9. Industry Classification Agent

Keep deterministic signature matching first.

Existing classifier:

```text
dataset
  |
  v
deterministic signature scoring
  |
  v
top N industry candidates
  |
  v
Industry Classification Agent
```

The agent must compare the candidates using evidence.

Output:

```python
class IndustryAssessment(BaseModel):
    selected_industry: str
    confidence: float
    evidence: list[Evidence]
    alternatives: list[IndustryCandidate]
    needs_human_confirmation: bool
```

The agent does not bypass industry-pack validation.

---

# 10. Schema Binding Agent

Continue using canonical roles.

Example:

```text
physical column:
    net_sales

canonical role:
    revenue_amount
```

Structured output:

```python
class BindingProposal(BaseModel):
    canonical_role: str
    physical_column: str
    evidence: list[Evidence]
    confidence: float
    alternatives: list[str]
    needs_human_confirmation: bool
```

The deterministic binding scorer and gate remain authoritative.

Do not allow the agent to directly write `schema_bindings.json`.

The agent proposes.

The binding service validates and commits.

---

# 11. KPI Discovery Agent

This agent can propose customer-specific KPIs and metrics.

It must NOT write executable SQL.

Correct:

```text
Agent:
  "Suggest repeat purchase rate."

      |
      v

KPI proposal:
  name
  definition
  required roles
  business rationale
  aggregation semantics
  confidence

      |
      v

Deterministic KPI compiler
      |
      v

sqlglot validation
      |
      v

DuckDB dry run
```

Incorrect:

```text
Agent -> raw SQL -> customer plugin
```

---

# 12. Validation Diagnostic Agent

When the deterministic 10-check harness fails, create a structured diagnosis.

```python
class ValidationDiagnosis(BaseModel):
    failed_check: str
    category: Literal[
        "schema",
        "sql_safety",
        "execution",
        "pii",
        "plugin_spec",
        "mcp",
        "hooks",
        "plausibility",
        "semantic"
    ]
    root_cause: str
    evidence: list[Evidence]
    repairable: bool
    recommended_action: str
    confidence: float
```

Only allow whitelisted repairs.

The diagnostic agent may recommend a repair; it should not directly modify arbitrary production code.

---

# 13. Agent Tool Security

Every agent tool must receive tenant context.

Do not expose a tool such as:

```python
run_sql(sql)
```

to an agent with unrestricted database access.

Use:

```python
run_readonly_query(
    tenant_id,
    datasource_ref,
    query,
    row_limit=...
)
```

The tool must enforce:

- read-only SQL
- query timeout
- row limits
- tenant isolation
- denied/PII columns
- safe parameterization
- query logging
- resource limits

The agent cannot override these policies.

---

# 14. Evidence Model

Every important agent decision should store evidence.

Example:

```python
class Evidence(BaseModel):
    type: Literal[
        "schema",
        "statistics",
        "sample",
        "relationship",
        "value_set",
        "industry_pack",
        "query_result",
        "validation"
    ]
    source: str
    observation: str
```

Example binding:

```json
{
  "canonical_role": "revenue_amount",
  "physical_column": "net_sales",
  "confidence": 0.94,
  "evidence": [
    {
      "type": "schema",
      "source": "column_profile",
      "observation": "decimal monetary column"
    },
    {
      "type": "statistics",
      "source": "column_statistics",
      "observation": "positive monetary distribution"
    }
  ]
}
```

This makes decisions auditable.

---

# 15. Confidence Policy

Never trust an agent's self-reported confidence as the final confidence.

Use:

```text
agent semantic score
+
deterministic evidence score
+
validation evidence
+
business rule score
=
final confidence
```

Suggested initial gates:

```text
>= 0.90
    auto-approve when no policy violation exists

0.70 - 0.89
    human confirmation

< 0.70
    investigate more or ask human
```

Make these thresholds configurable.

Do not hard-code them into prompts.

---

# 16. Human-in-the-Loop

Use LangGraph interrupts for agent-level human approval.

The UI remains the existing React wizard.

Flow:

```text
LangGraph
   |
   v
interrupt
   |
   v
persist run state
   |
   v
FastAPI/SSE
   |
   v
React review UI
   |
   v
POST decision
   |
   v
resume graph
```

For production durability, the enclosing Temporal workflow must also be designed to survive worker restarts and long approval periods.

---

# 17. Temporal Architecture

Create:

```text
workers/
  temporal_worker/
    worker.py
    workflows/
      forge_generation.py
    activities/
      ingest.py
      profile.py
      run_langgraph.py
      compile.py
      generate.py
      validate.py
      package.py
```

Suggested conceptual workflow:

```python
@workflow.defn
class ForgeGenerationWorkflow:
    @workflow.run
    async def run(self, run_id: str):
        await execute_activity(ingest)
        await execute_activity(profile)
        await execute_activity(run_langgraph_reasoning)
        await execute_activity(compile)
        await execute_activity(generate)
        await execute_activity(validate)
        await execute_activity(package)
```

Important:

- Keep workflow code deterministic.
- Put network/database/LLM calls in Activities.
- Configure explicit Activity retry policies.
- Never make direct external API calls from deterministic Temporal workflow code.

Temporal is used because production jobs must survive crashes, infrastructure failures, retries and long-running execution.

---

# 18. Model Abstraction

Create:

```text
agentic/model_policy.py
```

The application should be able to choose models by task.

Suggested initial policy:

```text
Data Understanding:
    strongest reasoning model

Industry Classification:
    medium/strong model

Schema Binding:
    strong structured-output model

KPI Discovery:
    strong reasoning model

Validation Diagnosis:
    strong reasoning model
```

Do not optimize purely for benchmark scores.

Track:

- correctness
- latency
- token cost
- tool calls
- retry count
- human override rate

Keep provider integrations behind one internal factory.

Example:

```python
def get_model(task: AgentTask) -> BaseChatModel:
    ...
```

---

# 19. Do Not Add RAG Yet

Do NOT introduce a vector database merely because this is an agentic system.

The first version should rely on:

- schema
- statistics
- sampled data
- relationships
- industry packs
- KPI definitions
- deterministic investigation queries

Add vector/hybrid retrieval only when there is a real corpus of:

- customer business terminology
- documentation
- historical approved mappings
- industry reference material

---

# 20. Storage Architecture

Use:

```text
Postgres
  -> users
  -> tenants
  -> runs
  -> stage_events
  -> approvals
  -> bindings
  -> validation_reports
  -> plugin_versions
  -> audit_logs

Object Storage
  -> original uploads
  -> normalized artifacts
  -> generated plugin ZIPs
  -> reports
```

Do not store large uploaded files directly in Postgres.

---

# 21. Existing Components to Preserve

Do NOT remove the existing:

```text
industry-packs/
packages/forge-core/
packages/mcp-runtime/
sqlglot compiler
DuckDB execution
Jinja templates
PII denial
SQL safety validation
plugin spec validation
MCP smoke tests
hooks smoke tests
plausibility checks
tenant isolation
RunRecord
StageEvent
```

The redesign is an orchestration upgrade, not a rewrite of the deterministic core.

---

# 22. Existing Components to Refactor

## `orchestrator.py`

Convert from the primary state-machine implementation to a thin compatibility/service layer around the new ForgeGraph + Temporal workflow.

## `agentic/binding_agent.py`

Refactor into a proper LangChain structured-output agent.

## `agentic/data_agent.py`

Refactor into the Data Understanding Agent.

## `agentic/data_understanding_agent.py`

Merge/restructure so there is one authoritative Data Understanding Agent implementation.

Avoid duplicate agent implementations.

## `agentic/investigation_tools.py`

Promote to the primary safe investigation-tool package.

## `agentic/memory.py`

Keep tenant-scoped, but distinguish:

- graph/checkpoint state
- long-term business memory

Do not store arbitrary cross-tenant conversation history.

---

# 23. API Changes

Keep existing run endpoints.

Internally change:

```text
POST /runs
POST /runs/upload
```

to start a Temporal workflow.

Keep:

```text
GET /runs/{id}
GET /runs/{id}/events
POST /runs/{id}/review
POST /runs/{id}/confirm-industry
POST /runs/{id}/confirm-bindings
GET /runs/{id}/report
GET /runs/{id}/download
POST /runs/{id}/publish/github
```

where practical.

The web UI should not need to know that LangGraph or Temporal exists.

---

# 24. Validation Architecture

Keep the 10 checks:

```text
1. schema facts
2. SQL safety
3. DuckDB dry-run
4. PII scan
5. plugin spec
6. Claude plugin validation
7. MCP smoke
8. LLM self-critique
9. hooks smoke
10. plausibility
```

Then add agent evaluation outside the customer validation path:

```text
Agent evaluation:
- binding accuracy
- industry classification
- KPI correctness
- trajectory/tool-use quality
- cost
- latency
- human correction rate
```

Do not make LangSmith evaluations the only production validator.

---

# 25. Testing Requirements

Every new agent must have:

## Unit tests

Test tools independently.

```text
test_schema_tools.py
test_binding_scorer.py
test_confidence_gate.py
```

## Agent contract tests

Given fixture X:

```text
expected structured output
```

## Golden datasets

Create:

```text
fixtures/evaluation/
    retail/
    finance/
    healthcare/
    edtech/
    generic/
```

Each benchmark should contain:

```text
dataset
expected industry
expected bindings
expected KPI semantics
expected denied columns
expected validation outcome
```

## Regression tests

Every change to:

- model
- prompt
- tool
- agent logic
- pack
- compiler

must be testable against the golden suite.

---

# 26. Observability

Keep user-facing run stages.

Add agent trace metadata:

```text
run_id
tenant_id
agent
model
prompt/version
tool calls
tool latency
tokens
cost
structured result
confidence
human override
```

Never log raw sensitive customer data unnecessarily.

Redact:

- passwords
- connection strings
- API keys
- PII
- raw database credentials
- secrets

---

# 27. Environment Variables

Create/update:

```text
DATABASE_URL=

TEMPORAL_ADDRESS=
TEMPORAL_NAMESPACE=
TEMPORAL_TASK_QUEUE=

GEMINI_API_KEY=

# optional
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

LANGSMITH_API_KEY=
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=

AZURE_STORAGE_ACCOUNT_URL=
AZURE_STORAGE_CONTAINER=

JWT_SECRET=

RUNS_DIR=
```

Never commit secrets.

Use `.env.example`, never `.env`.

---

# 28. Docker Compose Development Stack

Development should include at least:

```text
postgres
temporal
temporal-ui
api
temporal-worker
web
```

DuckDB remains embedded in Python and does not require a service.

Object storage can initially use:

- Azure Blob in an Azure environment, or
- a local S3-compatible emulator only if necessary for testing.

Do not add infrastructure without an actual need.

---

# 29. Implementation Order

Implement in this order.

## Phase 1 - Foundations

1. Add `langchain`.
2. Add `langgraph`.
3. Add provider integration package.
4. Create typed agent schemas.
5. Create safe investigation tools.
6. Create `ForgeState`.
7. Create ForgeGraph skeleton.

## Phase 2 - Agents

8. Implement Data Understanding Agent.
9. Implement Industry Classification Agent.
10. Implement Schema Binding Agent.
11. Implement KPI Discovery Agent.
12. Implement Validation Diagnostic Agent.

## Phase 3 - Deterministic integration

13. Connect graph outputs to existing deterministic binding gate.
14. Connect KPI proposals to deterministic compiler.
15. Connect compiler output to existing validator.
16. Add allowed repair loop.

## Phase 4 - Human-in-the-loop

17. Add LangGraph interrupts.
18. Connect interrupts to existing React review UI.
19. Implement resume.

## Phase 5 - Temporal

20. Add `temporalio`.
21. Create Temporal workflow.
22. Move long-running production execution behind Temporal.
23. Configure retries/timeouts.
24. Keep workflows deterministic.

## Phase 6 - Persistence

25. Standardize PostgreSQL production configuration.
26. Add object storage abstraction.
27. Move uploads/generated artifacts to durable storage.

## Phase 7 - Evaluation

28. Add LangSmith tracing.
29. Build golden datasets.
30. Add regression evaluation.
31. Compare model configurations.

---

# 30. Definition of Done

The V2 implementation is complete only when:

- [ ] A dataset can be uploaded.
- [ ] The job starts through Temporal.
- [ ] LangGraph owns agentic orchestration.
- [ ] Data Understanding Agent uses investigation tools.
- [ ] Industry classification can request human confirmation.
- [ ] Schema binding can request human confirmation.
- [ ] All agent outputs are structured Pydantic objects.
- [ ] Evidence is stored for important decisions.
- [ ] Confidence thresholds are deterministic and configurable.
- [ ] KPI proposals never directly execute agent-generated SQL.
- [ ] SQL is compiled deterministically.
- [ ] sqlglot validation still runs.
- [ ] DuckDB dry-run still runs.
- [ ] Existing 10-check validation still runs.
- [ ] Validation failures can be diagnosed by an agent.
- [ ] Only controlled repairs can be applied.
- [ ] MCP runtime remains generic.
- [ ] Tenant isolation is enforced in every data tool.
- [ ] Uploaded/generated files use durable storage.
- [ ] Production metadata is in Postgres.
- [ ] Human approval survives worker restarts.
- [ ] Agent traces can be inspected.
- [ ] Golden evaluation datasets pass.
- [ ] Existing plugin output compatibility is preserved.
- [ ] Existing security constraints remain enforced.

---

# 31. Explicit Anti-Patterns

Do NOT implement any of the following:

### Anti-pattern 1

```text
One giant agent that does everything.
```

### Anti-pattern 2

```text
LLM generates raw executable SQL and sends it to production.
```

### Anti-pattern 3

```text
Agent gets arbitrary database credentials.
```

### Anti-pattern 4

```text
Agent confidence automatically bypasses deterministic gates.
```

### Anti-pattern 5

```text
Every dataset is embedded into a vector database.
```

### Anti-pattern 6

```text
FastAPI BackgroundTasks is treated as durable workflow infrastructure.
```

### Anti-pattern 7

```text
All state is stored inside agent chat history.
```

### Anti-pattern 8

```text
Every problem is solved by adding another agent.
```

### Anti-pattern 9

```text
Agent directly modifies production plugin source files without validation.
```

---

# 32. Design Principle

The final system should behave like an:

> Agentic compiler for customer-specific analytics plugins.

The agentic side handles:

```text
discovery
reasoning
classification
semantic mapping
KPI discovery
diagnosis
```

The deterministic side handles:

```text
validation
SQL compilation
security
PII
plugin generation
packaging
MCP execution
```

This boundary is intentional and must be preserved.

---

# 33. Current Repository Compatibility

The implementation should adapt to the existing repository rather than deleting the existing architecture.

Current important modules include:

```text
forge-core
mcp-runtime
apps/api
apps/web
industry-packs
validation/
agentic/
models/
```

Existing pipeline:

```text
INGEST
PROFILE
CLASSIFY
BIND
COMPILE_KPIS
GENERATE
PACKAGE
VALIDATE
```

Keep those concepts.

The V2 work is primarily:

```text
existing deterministic pipeline
        +
LangGraph agentic reasoning
        +
Temporal durability
        +
typed state/evidence
        +
evaluation
```

---

# 34. Final Technology Stack

```text
Frontend
  React
  TypeScript
  Vite

API
  FastAPI
  Pydantic v2
  SQLAlchemy 2
  Alembic

Workflow
  Temporal
  temporalio

Agent orchestration
  LangGraph
  LangChain
  LangChain provider integrations

Data
  DuckDB
  PostgreSQL
  sqlglot

Storage
  Azure Blob Storage (production on Azure)

Generation
  Jinja2
  Python packaging/ZIP tooling

MCP
  Official Python MCP SDK

Testing
  pytest
  pytest-asyncio
  pytest-cov
  Ruff
  mypy

Agent observability/evaluation
  LangSmith

Deployment
  Docker
  Docker Compose for development
  Azure production services
```

---

# 35. Implementation Directive

The implementation agent must:

1. Inspect the existing repository before changing code.
2. Reuse existing deterministic components whenever their responsibilities are still correct.
3. Avoid duplicate implementations.
4. Avoid rewriting working ingestion/compiler/validation code merely to use LangChain.
5. Introduce LangGraph incrementally.
6. Preserve existing API contracts where feasible.
7. Add tests before changing critical behavior.
8. Keep all agent outputs structured.
9. Keep all customer data access behind authenticated/tenant-scoped tools.
10. Run the full existing test suite after each major migration step.
11. Add new golden evaluation fixtures before optimizing prompts/models.
12. Document every new architectural dependency.
13. Never bypass the deterministic validation harness.
14. Never expose customer secrets to agent prompts.
15. Never commit credentials or uploaded customer data.

The final result should be a production-oriented hybrid system, not a generic autonomous agent.


# 36. Final Engineering Principle

When making implementation decisions, optimize for this outcome:

> **Any reasonable customer business dataset -> correct, secure, validated, installable Claude analytics capability with minimal human intervention.**

Do not optimize for:

- maximum number of agents
- maximum LangChain usage
- maximum use of LLM-generated code
- maximum number of infrastructure components
- adding RAG/vector databases without a demonstrated need

A technology is justified only when it improves one or more of:

- semantic accuracy
- reliability
- security
- recoverability
- observability
- evaluation quality
- time-to-plugin
- customer experience

The Antigravity implementation agent must preserve this product objective throughout the migration.
