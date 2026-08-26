# Data2plugin — Context Discovery Agent Implementation Specification

## Purpose

Build a production-ready **Business Context Discovery Agent** inside Data2plugin.

This agent is responsible for understanding a customer's dataset in both:

1. **technical/data terms**, and
2. **business terms**.

It must inspect the data, form evidence-backed hypotheses, identify ambiguity, ask the customer targeted cross-questions, incorporate the answers, and produce a structured business-context model that downstream Data2plugin stages can trust.

This is **not** a generic chatbot and it is **not** the final plugin generator.

The agent exists to answer:

> "What does this data actually represent in the customer's business, what are the important entities/processes/metrics, what is uncertain, and what do we need to ask the data owner before building the plugin?"

---

# 1. Product Context

Data2plugin converts heterogeneous customer business data into a secure, validated, customer-specific Claude/MCP analytics capability.

The current pipeline is conceptually:

```text
Customer Data
   ↓
INGEST
   ↓
PROFILE
   ↓
UNDERSTAND
   ↓
CLASSIFY
   ↓
BIND
   ↓
COMPILE KPIs
   ↓
GENERATE
   ↓
VALIDATE
   ↓
PACKAGE
   ↓
Claude/MCP Plugin
```

The new Context Discovery Agent belongs primarily in:

```text
PROFILE
   ↓
BUSINESS CONTEXT DISCOVERY AGENT
   ↓
CLASSIFY / BIND / KPI DISCOVERY
```

It should become the authoritative source for business-context understanding.

Do not rewrite working ingestion, compiler, MCP runtime, packaging, or deterministic validation logic just to add this agent.

---

# 2. Primary Objective

Transform:

```text
Unknown customer dataset
```

into:

```text
Structured, evidence-backed business context
```

through:

```text
automatic profiling
      ↓
semantic investigation
      ↓
hypothesis formation
      ↓
targeted customer questions
      ↓
customer answers
      ↓
updated model
      ↓
readiness gate
```

The final output must be usable by:

```text
Industry Classification Agent
Schema Binding Agent
KPI Discovery Agent
Metric Compiler
Plugin Generator
Validation system
```

---

# 3. Critical Product Principle

Do not confuse:

```text
column name
```

with:

```text
business meaning
```

Example:

```text
amount
```

may mean:

- gross amount
- net amount
- revenue
- invoice total
- payment amount
- discount
- transaction value

The agent must gather evidence before deciding.

Likewise:

```text
phone_number
```

may be:

- the unique lead identifier,
- a contact attribute,
- a shared family number,
- a duplicated value across multiple interactions.

Never infer high-impact business semantics solely from naming.

---

# 4. Technology Requirements

Use the existing project stack and follow the V2 architecture.

## Required packages

Use `uv`.

```bash
uv add langchain langgraph
```

For Gemini, if Gemini remains the configured production model:

```bash
uv add langchain-google-genai
```

If the repository already has the relevant provider dependency, reuse it rather than adding a duplicate.

The agent must use:

- LangGraph for orchestration/state transitions
- LangChain `create_agent` or equivalent current LangChain agent API for the specialized agent
- Pydantic v2 for structured outputs
- existing DuckDB/data-access tooling
- existing tenant/security controls

Do not introduce a vector database for this agent unless the existing application already requires one.

---

# 5. Where the Agent Lives

Follow the existing repository structure.

Preferred structure:

```text
packages/
  forge-core/
    src/
      forge_core/
        agentic/
          agents/
            context_discovery.py

          graph/
            context_discovery_graph.py
            context_discovery_state.py
            context_discovery_routing.py

          prompts/
            context_discovery.py

          schemas/
            business_context.py
            business_questions.py
            evidence.py

          tools/
            context_tools.py
```

If equivalent modules already exist, adapt them instead of creating duplicates.

The repository already contains agentic modules such as:

```text
binding_agent.py
data_agent.py
data_understanding_agent.py
investigation_tools.py
memory.py
```

Inspect those first.

Consolidate overlapping functionality rather than creating a second competing implementation.

---

# 6. Responsibilities

The Context Discovery Agent MUST:

1. inspect the dataset,
2. inspect schema and profiling information,
3. identify likely record grain,
4. identify candidate entities,
5. investigate relationships,
6. identify important dimensions and measures,
7. identify time semantics,
8. identify business lifecycle/events,
9. detect data-quality anomalies,
10. form explicit hypotheses,
11. distinguish facts from hypotheses,
12. identify high-impact ambiguities,
13. ask targeted business questions,
14. incorporate customer answers,
15. produce structured business context,
16. decide whether the dataset is ready for downstream processing.

The agent MUST NOT:

- generate final executable SQL,
- bypass deterministic schema binding,
- bypass security/PII policies,
- access arbitrary tenant data,
- silently "fix" ambiguous business semantics,
- ask an unnecessary giant questionnaire,
- pretend an inference is confirmed fact.

---

# 7. Agent Behavior

## Phase A — Automatic investigation

Before asking the customer questions, investigate the dataset.

Use available tools to understand:

- row count
- column count
- column names
- data types
- null rate
- uniqueness
- cardinality
- distinct values
- distributions
- date ranges
- repeated identifiers
- candidate keys
- relationships
- suspicious values
- inconsistent labels
- representative rows
- likely domain signals

The agent should form hypotheses based on actual evidence.

---

# 8. Evidence Model

Every important conclusion should be backed by evidence.

Implement/reuse:

```python
class Evidence(BaseModel):
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
        "data_quality"
    ]
    source: str
    observation: str
```

Example:

```json
{
  "type": "statistics",
  "source": "phone_number_profile",
  "observation": "5530 rows but only 2260 unique phone numbers"
}
```

---

# 9. Facts vs Hypotheses vs Open Questions

The agent must maintain three distinct categories.

## Confirmed facts

Directly observed or explicitly confirmed by the user.

Example:

```text
One row represents one lead interaction.
```

## Inferred hypotheses

Reasonable interpretations supported by evidence but not confirmed.

Example:

```text
net_sales is probably the revenue measure.
```

## Open questions

Important unresolved business meaning.

Example:

```text
Does favorable call outcome mean successful contact or actual conversion?
```

Never promote a hypothesis to a confirmed fact without sufficient evidence.

---

# 10. Investigation Tools

Build/reuse safe tools.

At minimum the agent should be able to call tools equivalent to:

```text
inspect_schema
get_table_profile
inspect_column
get_column_statistics
get_distinct_values
get_value_distribution
get_null_profile
get_cardinality
get_duplicate_profile
sample_rows
inspect_date_profile
detect_candidate_keys
detect_candidate_relationships
inspect_relationship
run_safe_duckdb_query
```

Optional high-value tools:

```text
compare_columns
compare_value_sets
detect_inconsistent_categories
detect_mixed_types
detect_suspicious_dates
detect_outliers
```

## Tool requirements

Every tool must:

- be tenant-scoped,
- enforce read-only access,
- enforce row limits,
- enforce query timeouts,
- avoid leaking secrets,
- apply denied/PII policies where relevant,
- log tool usage,
- return structured results.

Never expose unrestricted database credentials to an LLM.

---

# 11. Tool Usage Strategy

Use a staged strategy.

### Step 1 — Schema

```text
inspect_schema
```

### Step 2 — High-level profiling

```text
get_table_profile
get_null_profile
get_cardinality
```

### Step 3 — Important columns

Inspect likely:

- identifiers
- dates
- status fields
- numeric measures
- categorical dimensions
- fields with ambiguous names

### Step 4 — Relationships

Look for:

```text
primary key
foreign keys
repeated identifiers
entity relationships
```

### Step 5 — Representative evidence

Use:

```text
sample_rows
distinct_values
value_distribution
```

### Step 6 — Hypothesis generation

Only after enough evidence exists.

Avoid repeated queries that do not change the conclusion.

---

# 12. Core Business Questions to Resolve

The agent must prioritize these concepts.

## A. Record grain

Ask:

> What does one row represent?

Possible answers:

- one customer
- one lead
- one call
- one interaction
- one order
- one transaction
- one appointment
- one ticket
- one event

This is one of the highest-priority questions.

---

## B. Entity identity

Ask:

> What uniquely identifies the important business entity?

Examples:

```text
customer_id
lead_id
order_id
transaction_id
phone_number
email
```

If repeated identifiers exist, explicitly show evidence.

Example:

```text
"I found 5,530 records but only 2,260 unique phone numbers. Does that mean multiple records belong to the same lead?"
```

---

## C. Business process

Determine the lifecycle.

For example:

```text
Lead Created
   ↓
Contacted
   ↓
Trial Scheduled
   ↓
Trial Attended
   ↓
Purchased
```

Do not assume that is the process.

Ask the customer to confirm or correct the observed lifecycle.

---

## D. Business objective

Ask:

> What business decisions should this data help you make?

Examples:

- increase conversion,
- improve campaign performance,
- improve sales agent performance,
- reduce churn,
- improve operational efficiency,
- monitor revenue,
- find high-quality leads.

---

## E. Success / conversion

Ask:

> What exactly counts as success or conversion in your business?

Do not infer from labels like:

```text
favorable
successful
closed
completed
positive
```

unless the business meaning has been confirmed.

---

## F. Important field semantics

For ambiguous columns, ask evidence-based questions.

Bad:

```text
"What is category?"
```

Good:

```text
"I found category values such as 'guitar', 'Guitar', 'Western Vocals', and 'Western vocals'. Is this the product/course the lead is interested in, and should the different casing variants be treated as the same category?"
```

---

## G. Time semantics

Clarify:

- timezone,
- date meaning,
- creation date vs event date,
- historical vs current records,
- reporting period,
- ambiguous date formats.

Do not silently normalize questionable dates.

---

## H. Important KPIs and business questions

Ask:

> What are the top business questions you want the generated Claude plugin to answer?

Prefer questions over asking the user to design a KPI schema.

Example:

```text
Which campaign produces the most qualified leads?
Which agent has the highest conversion rate?
How many trials were attended last month?
Which products are declining?
```

The agent can later convert these into candidate KPI definitions.

---

# 13. Adaptive Interview

Do not ask 20–30 questions at once.

Start with approximately 3–8 high-impact questions.

After the user answers:

1. update the business context,
2. remove resolved ambiguities,
3. identify new contradictions,
4. ask the next necessary questions.

Stop when the model is sufficiently reliable for downstream stages.

The number of questions should be determined by uncertainty, not by a fixed count.

---

# 14. Question Selection Algorithm

Every potential question should have a reason.

Internally score a question based on:

```text
impact on correctness
+
uncertainty
+
downstream dependency
+
likelihood of changing KPI/plugin behavior
-
user burden
```

Ask questions with the highest expected information gain first.

Do not ask low-impact questions.

---

# 15. Evidence-Based Question Format

Customer-facing questions should include the evidence that triggered them.

Preferred format:

```text
I found 5,530 records but only 2,260 unique phone numbers.

This suggests that multiple records may belong to the same lead.

Question:
Does each row represent a separate interaction/call for a lead, or should each row be treated as a separate lead?

Options:
A. One row = one lead
B. One row = one interaction/call
C. One row = one appointment/trial
D. Other
```

Allow free-text correction.

Do not force the user into incorrect predefined options.

---

# 16. Data Quality Handling

Detect:

- inconsistent casing,
- inconsistent categories,
- mixed types,
- invalid dates,
- suspicious dates,
- duplicate IDs,
- missing important fields,
- implausible values,
- malformed identifiers,
- numeric strings,
- conflicting semantics.

Behavior:

```text
detect
  ↓
explain
  ↓
determine business impact
  ↓
ask if material
  ↓
record decision
```

Do not automatically clean business semantics without confirmation.

Technical normalization may be performed deterministically if it cannot change meaning.

---

# 17. Business Context Schema

Use or adapt existing models, but the output must contain the equivalent of:

```python
class BusinessContext(BaseModel):
    domain: str | None
    business_objective: str | None
    dataset_purpose: str | None

    record_grain: str | None

    primary_entities: list[EntityDefinition]
    relationships: list[RelationshipDefinition]

    business_process: BusinessProcessDefinition | None
    lifecycle_events: list[LifecycleEvent]

    important_dimensions: list[BusinessField]
    important_measures: list[BusinessField]
    time_semantics: list[TimeField]

    status_definitions: list[StatusDefinition]
    success_definition: SuccessDefinition | None

    candidate_kpis: list[BusinessKPI]
    desired_questions: list[str]

    data_quality_issues: list[DataQualityIssue]

    confirmed_facts: list[Evidence]
    inferred_hypotheses: list[Hypothesis]
    open_questions: list[BusinessQuestion]

    security_considerations: list[SecurityConsideration]

    overall_confidence: float
    ready_for_downstream_pipeline: bool
```

Do not create duplicate models if the existing repository already has equivalent types such as `DataUnderstanding`, `DomainAssessment`, `ColumnClaim`, `DataReview`, etc.

Prefer extending existing domain models.

---

# 18. Question Schema

Create/reuse a structured model:

```python
class BusinessQuestion(BaseModel):
    question_id: str
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
        "other"
    ]

    question: str
    context: str
    evidence: list[Evidence]

    options: list[str] = []
    required: bool = True

    impact: Literal["low", "medium", "high", "critical"]
```

The API/UI should render this cleanly.

---

# 19. Agent Graph

Do not implement this as one uncontrolled loop.

Use a LangGraph state graph conceptually like:

```text
START
  ↓
LOAD_PROFILE
  ↓
ANALYZE_EVIDENCE
  ↓
BUILD_HYPOTHESES
  ↓
IDENTIFY_INFORMATION_GAPS
  ↓
DECIDE_NEXT_ACTION
  ├── investigate_more → TOOL_INVESTIGATION → ANALYZE_EVIDENCE
  ├── ask_user         → INTERRUPT / HUMAN_REVIEW
  └── ready             → FINALIZE_CONTEXT
                         ↓
                         END
```

After a human answer:

```text
HUMAN_REVIEW
    ↓
INCORPORATE_ANSWER
    ↓
ANALYZE_EVIDENCE
```

This should be a bounded workflow.

Do not allow infinite agent loops.

---

# 20. LangGraph State

Use a dedicated typed state.

Example:

```python
class ContextDiscoveryState(TypedDict, total=False):
    run_id: str
    tenant_id: str
    datasource_ref: str

    schema_profile: SchemaProfile | None
    evidence: list[Evidence]

    confirmed_facts: list[Evidence]
    hypotheses: list[Hypothesis]
    open_questions: list[BusinessQuestion]

    user_answers: list[BusinessAnswer]

    business_context: BusinessContext | None

    iteration_count: int
    tool_call_count: int

    ready_for_downstream_pipeline: bool
```

Keep state bounded and serializable.

Do not store unnecessary raw customer data inside graph state.

---

# 21. Readiness Gate

The agent must not declare success simply because the dataset was successfully profiled.

Mark:

```text
ready_for_downstream_pipeline = true
```

only when the following are sufficiently understood:

- record grain,
- primary entities,
- entity identity,
- key business process,
- important field semantics,
- high-impact ambiguity,
- success/conversion definition when relevant,
- material data-quality issues,
- intended business use/questions.

Otherwise:

```text
ready_for_downstream_pipeline = false
```

and return the remaining high-priority questions.

---

# 22. Handoff Contract

The downstream agents should consume:

```text
BusinessContext
+
Evidence
+
ConfirmedFacts
+
KnownDataQualityIssues
```

They should not need to repeat basic business-context discovery.

Example:

```text
Context Discovery
      ↓
BusinessContext
      ↓
Industry Classification
      ↓
Schema Binding
      ↓
KPI Discovery
```

This prevents redundant agent calls.

---

# 23. Security

The agent must never request:

- passwords,
- database credentials,
- API keys,
- secrets.

Do not place unnecessary PII into:

- prompts,
- agent memory,
- LangSmith traces,
- persistent graph state.

Use tenant-scoped data access.

Respect existing denied-column/PII policies.

---

# 24. Prompt Implementation

Create the agent system prompt in:

```text
packages/forge-core/src/forge_core/agentic/prompts/context_discovery.py
```

The system prompt should include the following core content:

```text
You are the Data2plugin Business Context Discovery Agent.

Your job is to understand a customer's dataset in technical and business context before the system generates a customer-specific Claude/MCP plugin.

Never assume that column names equal business meanings.

Investigate first.

Use structural evidence, data evidence, and customer-provided business evidence.

Maintain:
- confirmed facts
- inferred hypotheses
- open questions

When uncertainty can materially affect entities, grain, KPIs, conversion, metrics, or plugin behavior, ask a targeted customer question.

Do not ask a generic questionnaire.

Ask only the highest-value questions first.

Use the observed data in your questions.

Example:
"I found 5,530 records but only 2,260 unique phone numbers. Does this mean multiple rows are interactions belonging to the same lead?"

Do not silently fix business ambiguity.

Do not generate executable SQL.

Do not bypass deterministic validation.

Your goal is not to know everything.
Your goal is to know enough, with sufficient evidence, for downstream Data2plugin stages to safely and accurately build the plugin.

Do not mark the dataset ready until critical business semantics are sufficiently resolved.

Return structured Pydantic-compatible output.
```

The implementation may expand this prompt with repository-specific rules, tool descriptions, safety requirements, and current industry-pack information.

Do not put the entire dataset into the system prompt.

---

# 25. Example for the Existing `sparda_leads.csv` Dataset

This dataset is included only as a development/evaluation example if the repository has access to it.

Observed characteristics include fields related to:

```text
leads
campaigns
agents
trial dates/times
callbacks
call outcomes
lead scores
sentiment
categories
```

A good agent should not immediately invent a KPI model.

It should detect questions such as:

```text
1. What does one row represent: a lead, a call, a trial, or another interaction?

2. I found multiple rows associated with the same phone number.
   Does a phone number identify one lead, with multiple interaction records?

3. What exactly does outcome_of_the_call = favorable mean?
   Does it mean successful contact, agreement to a next step, trial booking, or actual conversion?

4. What event defines a converted/successful lead?

5. Does category represent the course/instrument/product the lead is interested in?

6. Should values such as "guitar" and "Guitar" be treated as the same category?

7. What does leads_score represent and what does a high score mean?

8. What are the top business questions you want the generated Claude plugin to answer?
```

These questions are examples of the intended behavior, not hardcoded questions.

The agent must generate questions dynamically from observed evidence.

---

# 26. Testing

Add tests for:

## Tool tests

```text
test_context_tools.py
```

Verify:

- correct tenant scope
- row limits
- timeouts
- structured output
- safe SQL

## Prompt/agent contract tests

Verify the agent returns valid structured output.

## Question-generation tests

Given known ambiguity, verify the expected question category and presence of evidence.

Example:

```text
duplicate identifier
→ record grain/entity identity question
```

## Golden datasets

Create:

```text
fixtures/evaluation/context_discovery/
    retail/
    finance/
    healthcare/
    edtech/
    generic/
```

Each fixture should define expected:

- record grain
- primary entity
- important relationships
- domain
- business process
- critical questions
- data-quality findings

## Regression evaluation

Changing:

- model,
- prompt,
- tool behavior,
- context schema,
- agent routing

must run the golden suite.

---

# 27. Observability

Record:

```text
run_id
tenant_id
agent
model
tool calls
tool latency
question count
answer count
iteration count
confidence
ready/not-ready status
human overrides
```

Do not log unnecessary raw customer data.

Keep user-facing stage events separate from internal agent traces.

---

# 28. Acceptance Criteria

This feature is complete when:

- [ ] Agent automatically profiles the dataset before asking questions.
- [ ] Agent can identify candidate record grain.
- [ ] Agent can identify candidate entities.
- [ ] Agent can detect duplicate/repeated identifiers.
- [ ] Agent can inspect categorical inconsistencies.
- [ ] Agent can detect suspicious dates/types/values.
- [ ] Agent maintains facts, hypotheses, and open questions separately.
- [ ] Agent generates evidence-based questions.
- [ ] Questions are adaptive rather than a fixed questionnaire.
- [ ] Questions are limited to high-value ambiguities.
- [ ] User answers can be incorporated into graph state.
- [ ] Agent can continue discovery after human answers.
- [ ] Agent has a readiness gate.
- [ ] Agent produces structured BusinessContext.
- [ ] Downstream agents can consume BusinessContext.
- [ ] Agent cannot bypass tenant/security controls.
- [ ] Agent cannot emit directly executable customer SQL.
- [ ] Agent does not require a vector database.
- [ ] Golden evaluation datasets exist.
- [ ] Existing tests still pass.

---

# 29. Implementation Directive for Antigravity

Before coding:

1. Inspect the entire existing agentic package.
2. Inspect existing Pydantic models.
3. Inspect current profiling tools.
4. Inspect existing `RunRecord`, `DataUnderstanding`, `ColumnClaim`, `DataReview`, and binding models.
5. Inspect existing APIs for review/questions.
6. Identify what can be reused.
7. Do not create duplicate agent/tool/model implementations.

Then implement:

```text
1. schemas
2. investigation tools
3. LangGraph state
4. LangGraph graph
5. Context Discovery Agent
6. system prompt
7. human-question interrupt integration
8. readiness gate
9. downstream handoff
10. tests/evaluation fixtures
```

After implementation:

```text
run formatter
run type checks
run unit tests
run integration tests
run golden context-discovery evaluations
```

Report:

- files changed,
- packages added,
- APIs changed,
- tests added,
- known limitations,
- sample agent interaction.

Do not stop at scaffolding.

The feature must be integrated into the real Data2plugin pipeline.

---

# 30. Final Engineering Rule

The agent should behave like a strong business/data analyst sitting between the raw dataset and the plugin compiler.

Its core loop is:

```text
OBSERVE
  ↓
REASON
  ↓
FORM HYPOTHESIS
  ↓
IDENTIFY UNCERTAINTY
  ↓
ASK TARGETED QUESTION
  ↓
INCORPORATE ANSWER
  ↓
VALIDATE UNDERSTANDING
  ↓
HAND OFF STRUCTURED BUSINESS CONTEXT
```

The quality of the generated plugin is downstream of the quality of this understanding.

Therefore:

> **Do not rush from schema to plugin. First understand the business represented by the data.**
