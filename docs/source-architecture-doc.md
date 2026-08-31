# MIS-to-Claude Plugin Generator --- System Architecture & Build Plan

**Prepared as:** Architecture plan (research-backed,
implementation-oriented)\
**Target runtime:** Claude Desktop / Claude Code plugin ecosystem\
**Generation engine:** Gemini (meta-LLM for generating skills, agents,
recipes, and customer-specific configuration)

------------------------------------------------------------------------

> **Amendments (improvement plan).** Since this doc was written: the LLM
> understanding phase is **mandatory** (no `--no-llm` / deterministic-only
> run) and now includes a synthesis pass that ships `config/schema_model.json`
> — per-table docs, enum decodes, pattern notes, and a dry-run-verified
> NL→SQL cookbook — served to the client as `schema://` MCP resources.
> Profiling gained composite-key grain, generous relationship recall
> (`strong`/`weak`, empty when unrelated), value-set capture, and
> statistical pattern mining. **All** ingested tables are queryable, not
> just the pack's primary fact table; relationships are optional. The
> `is_likely_pii` heuristic, sample redaction, and the `pii_scan` validation
> check were **removed** — the harness is 7 checks. See
> [architecture.md](architecture.md) and [generator-flow.md](generator-flow.md)
> for the current flow.

------------------------------------------------------------------------

## 1. Executive Summary

The product is a **meta-product / plugin compiler**.

The input is a customer's MIS data source:

-   CSV / TSV
-   Excel
-   JSON / NDJSON
-   Parquet
-   PostgreSQL / MySQL / SQLite
-   Snowflake / BigQuery
-   Other supported read-only data sources

The output is not an answer. The output is a **fully formed, validated,
installable Claude Code/Desktop plugin** custom-built for that customer.

The generated plugin can contain:

-   **Skills** --- domain-specific instructions and analytical playbooks
-   **Subagents / Agents** --- specialized reasoning workers such as SQL
    Analyst, Retention Analyst, and Report Generator
-   **MCP configuration** --- customer-specific configuration for a
    reusable generic MCP server
-   **Commands** --- explicit entry points for common workflows
-   **Hooks** --- optional event-driven validation/automation
-   **Recipes** --- reusable multi-step workflows
-   **Artifacts** --- dashboard/chart/report scaffolding
-   **Configuration** --- schema bindings, KPI definitions, guardrails,
    and customer metadata

### Core architectural principle

> **The Plugin is the generated product. Skills, Agents, MCP, Commands,
> Hooks, Recipes, and Artifacts are components of that product.**

The system should **generate configuration and instructions, not
arbitrary executable infrastructure** wherever possible.

In particular, Gemini should not generate a bespoke MCP server
implementation for every customer. Instead, the platform should maintain
**one robust generic MCP server** and generate customer-specific
configuration such as:

-   `schema_bindings.json`
-   `kpi_defs.json`
-   query allow-lists
-   customer-specific metadata
-   tool/skill bindings

This makes the system safer, easier to validate, easier to upgrade, and
significantly easier to maintain.

The right mental model is:

> **This is a compiler with a QA department, not a chatbot.**

The system takes:

``` text
Customer Data Source + Industry Context
                ↓
        Schema / Data Profile
                ↓
        Industry Classification
                ↓
        Gemini Generation
                ↓
 Skills + Agents + MCP Config + Recipes + Artifacts
                ↓
       Validation & Cross-Verification
                ↓
        Installable Claude Plugin
```

------------------------------------------------------------------------

# 2. Grounding: How the Claude Plugin Architecture Maps to This Product

A Claude Code / Claude Desktop plugin is the **top-level distributable
artifact**.

A representative structure is:

``` text
mis-analytics/
├── .claude-plugin/
│   └── plugin.json
│
├── commands/
│   ├── analyze.md
│   ├── report.md
│   └── kpi.md
│
├── agents/
│   ├── sql-specialist.md
│   ├── data-analyst.md
│   └── report-generator.md
│
├── skills/
│   ├── data-analysis/
│   │   └── SKILL.md
│   ├── retention-analysis/
│   │   └── SKILL.md
│   └── report-generation/
│       └── SKILL.md
│
├── hooks/
│   └── hooks.json
│
├── config/
│   ├── schema_bindings.json
│   ├── kpi_defs.json
│   └── guardrails.json
│
├── .mcp.json
├── README.md
└── LICENSE
```

If the plugin is distributed through a marketplace, the marketplace
maintains the catalog/distribution metadata separately.

## 2.1 Plugin

The **Plugin** is the complete customer-facing package.

It is responsible for bundling the different capabilities into one
installable unit.

``` text
Plugin
├── Skills
├── Agents / Subagents
├── MCP configuration
├── Commands
├── Hooks
├── Recipes
├── Artifacts
└── Customer configuration
```

## 2.2 Skills

Skills are **progressive-disclosure instructions**.

They should explain:

-   when the capability should be used
-   how Claude should reason about the task
-   which MCP tools should be used
-   what analytical methodology should be followed
-   what safety/PII constraints apply
-   what output structure is expected

Skills are **not the executable data layer**.

For example:

``` text
skills/retention-analysis/SKILL.md
```

might instruct Claude to:

1.  inspect the schema
2.  identify customer/entity grain
3.  define cohort boundaries
4.  query the required fields
5.  calculate retention
6.  explain trends
7.  avoid exposing PII

## 2.3 Agents / Subagents

Subagents are specialized reasoning workers.

They should be used when a task benefits from an isolated context,
specialized instructions, or a dedicated responsibility.

Examples:

``` text
agents/
├── sql-specialist.md
├── data-analyst.md
├── retention-analyst.md
├── report-generator.md
└── dashboard-analyst.md
```

Recommended responsibility split:

  Agent               Responsibility
  ------------------- -----------------------------------------------------
  SQL Specialist      Schema-aware query construction and query reasoning
  Data Analyst        General MIS analysis and anomaly interpretation
  Retention Analyst   Cohorts, retention, repeat behavior
  Report Generator    Multi-step report generation
  Dashboard Analyst   KPI/chart selection and dashboard reasoning

### Important distinction

``` text
Plugin  = complete product/package
Agent   = specialized reasoning worker
Skill   = reusable instructions/playbook
MCP     = executable tool/data layer
```

Do not treat the subagent as a replacement for the plugin.

For this product, **subagents are components inside the generated
plugin**.

## 2.4 MCP

MCP is the executable tool layer.

The plugin's `.mcp.json` should point to a **generic, reusable MCP
server**.

Typical tools:

``` text
describe_schema
get_data_profile
run_safe_query
get_kpi
search_records
```

The critical architectural split is:

``` text
Skills / Agents
    ↓
decide what to do
    ↓
MCP tools
    ↓
actually execute operations
    ↓
Customer MIS database
```

The generated plugin should therefore contain customer-specific MCP
**configuration**, rather than a newly generated MCP implementation for
every customer.

## 2.5 Commands

Commands provide explicit entry points for common workflows.

Examples:

``` text
/mis-analytics:analyze
/mis-analytics:report
/mis-analytics:kpi
```

Commands should invoke or guide the appropriate skills/agents rather
than duplicating business logic.

## 2.6 Hooks

Hooks are optional event-driven automation.

Potential uses:

-   generated-file validation
-   query safety checks
-   audit logging
-   pre-execution checks
-   post-generation validation
-   customer-specific guardrails

Hooks should be added only when there is a clear lifecycle event that
requires them.

------------------------------------------------------------------------

# 3. Product Pipeline

The system should be implemented as five primary stages:

``` text
[1] INGEST
      ↓
[2] PROFILE
      ↓
[3] CLASSIFY
      ↓
[4] GENERATE
      ↓
[5] VALIDATE / CROSS-VERIFY / PACKAGE
```

Detailed view:

``` text
┌──────────────┐
│ 1. INGEST    │
│ CSV / DB /   │
│ Excel / etc. │
└──────┬───────┘
       ↓
┌──────────────┐
│ 2. PROFILE   │
│ deterministic│
│ + Gemini     │
└──────┬───────┘
       ↓
┌──────────────┐
│ 3. CLASSIFY  │
│ Industry +   │
│ intent       │
└──────┬───────┘
       ↓
┌──────────────┐
│ 4. GENERATE  │
│ Skills       │
│ Agents       │
│ MCP config   │
│ Recipes      │
│ Artifacts    │
└──────┬───────┘
       ↓
┌──────────────────────────┐
│ 5. VALIDATE / VERIFY     │
│ Schema                   │
│ SQL safety               │
│ Self-critique            │
│ Dry-run                  │
│ PII / compliance         │
│ Plugin structure         │
└──────────┬───────────────┘
           ↓
    Installable Plugin
```

The key design rule remains:

> **Allow useful exploration upstream; verify everything downstream.**

Nothing reaches the customer without validation.

------------------------------------------------------------------------

# 4. Component Architecture

## 4.1 Ingestion Layer

### Purpose

Normalize arbitrary input into one internal representation.

### Inputs

-   CSV / TSV
-   Excel
-   JSON / NDJSON
-   Parquet
-   PostgreSQL
-   MySQL
-   SQLite
-   Snowflake
-   BigQuery

### Output

A unified `DataSource` descriptor containing:

``` text
tables
row_counts
columns
raw_dtypes
sample_rows
connection_contract
```

For database sources, the platform should prefer a **read-only
connection contract** rather than copying the entire customer's dataset.

------------------------------------------------------------------------

# 4.2 Data Profiling Engine

The profiling layer should contain two complementary components.

## Layer 1 --- Deterministic Profiling

Use pandas/DuckDB or database-native introspection for mechanical facts.

For each column determine:

-   dtype
-   cardinality
-   null percentage
-   min/max
-   distribution
-   likely identifier
-   foreign-key candidate
-   timestamp
-   categorical
-   currency/amount
-   free text
-   geographic field
-   boolean
-   candidate joins
-   table grain

Output:

``` text
Structural Profile
```

This layer should be deterministic and should not depend on an LLM.

## Layer 2 --- Gemini Insight Exploration

Gemini receives:

-   Structural Profile
-   small representative sample
-   safe metadata
-   industry context if available

Gemini can propose:

-   semantic meaning for ambiguous fields
-   candidate KPIs
-   business patterns
-   likely central entities
-   data-quality concerns
-   useful analytical workflows

Gemini's output is **not trusted blindly**.

Every semantic claim should be attached to evidence and rechecked during
validation.

Output:

``` text
Schema Profile
├── Structural Profile
├── Semantic Annotations
├── Candidate KPIs
├── Candidate Insights
├── Data Quality Flags
└── Confidence / Evidence
```

The profiling boundary should also act as a privacy boundary.

Do not send unnecessary raw customer data to Gemini.

------------------------------------------------------------------------

# 4.3 Industry Classification Engine

### Purpose

Determine which Industry Pack best matches the customer's data.

Possible packs:

``` text
Healthcare / MIS
EdTech
Finance
Retail / E-commerce
Generic Analytics
```

Classification should primarily be a matching/routing problem.

Compare:

-   entity patterns
-   table shapes
-   column names
-   data types
-   relationship patterns
-   KPI signatures

If confidence is low:

``` text
Top 3 Industry Packs
        ↓
Customer confirmation
```

Support blended industries where necessary.

Example:

``` text
Healthcare + E-commerce
```

------------------------------------------------------------------------

# 4.4 Industry Knowledge Base

The Industry Pack library is one of the highest-value assets in the
system.

Each pack should be versioned and contain:

### Entity vocabulary

Example:

``` text
Healthcare:
patient
encounter
diagnosis
lab_result
provider
booking
```

### KPI library

Examples:

``` text
Revenue
Retention
Average Order Value
Average Length of Stay
PhenoAge
Conversion Rate
```

Each KPI must define:

-   formula
-   required fields
-   required grain
-   assumptions
-   validation rules
-   output format

### Skill templates

Examples:

``` text
Cohort Retention Analyst
Compliance Auditor
Report Generator
Data Quality Analyst
```

### Agent templates

Examples:

``` text
SQL Specialist
Healthcare Analyst
Retention Analyst
Report Analyst
```

### Recipe templates

Examples:

``` text
Monthly Sales Trend
Repeat Customer Analysis
Retention Breakdown
Monthly MIS Report
```

### Artifact templates

Examples:

``` text
KPI dashboard
Revenue trend
Retention cohort chart
Funnel
Executive report
```

### Guardrails

Examples:

``` text
PII protection
PHI protection
read-only access
financial caution
medical caution
aggregate-only reporting
```

------------------------------------------------------------------------

# 4.5 Generation Engine

Gemini generates the customer-specific plugin components.

Generation should be split into independently testable generators.

## Generator 1 --- Skill Generator

Produces:

``` text
skills/<skill-name>/SKILL.md
```

The skill must be grounded in:

-   actual schema
-   industry pack
-   supported MCP tools
-   validated KPI definitions
-   customer-specific analytical needs

## Generator 2 --- Agent Generator

Produces:

``` text
agents/<agent-name>.md
```

Each agent should define:

-   purpose
-   responsibilities
-   when it should be used
-   available tools
-   reasoning constraints
-   output expectations
-   safety restrictions
-   escalation/fallback behavior

Example:

``` text
SQL Specialist
    ↓
inspect schema
    ↓
reason about query
    ↓
call safe MCP query
    ↓
validate result
    ↓
return structured analysis
```

Agents should not bypass MCP safety controls.

## Generator 3 --- MCP Configuration Generator

Gemini generates configuration, not arbitrary server infrastructure.

Example:

``` text
config/
├── schema_bindings.json
├── kpi_defs.json
└── guardrails.json
```

The generic MCP server consumes these files.

The generated configuration can specify:

-   table bindings
-   column mappings
-   allowed columns
-   KPI definitions
-   query constraints
-   result limits
-   customer-specific aliases
-   business semantics

## Generator 4 --- Recipe Generator

Generates reusable multi-step workflows.

Example:

``` text
Monthly Retention Report

1. describe_schema
2. identify customer entity
3. calculate cohort metrics
4. calculate retention
5. compare with previous period
6. generate narrative
7. generate artifact
```

## Generator 5 --- Artifact Generator

Generates dashboard/chart/report scaffolding.

It should bind outputs to:

-   real MCP tool results
-   validated KPI definitions
-   real field names

Possible artifacts:

``` text
HTML
React
Charts
Executive dashboards
CSV summaries
Reports
```

------------------------------------------------------------------------

# 4.6 Generic MCP Server Architecture

This is a critical architectural decision.

### Do NOT build:

``` text
Customer A
    ↓
Gemini generates Python MCP server

Customer B
    ↓
Gemini generates another Python MCP server

Customer C
    ↓
Gemini generates another Python MCP server
```

Instead build:

``` text
                    Generic MCP Server
                           │
            ┌──────────────┼──────────────┐
            ↓              ↓              ↓
     schema_bindings   kpi_defs      guardrails
            │              │              │
            └──────────────┼──────────────┘
                           ↓
                     Customer DB
```

One robust MCP implementation can serve many customers.

Customer-specific behavior is configuration-driven.

This gives:

-   easier upgrades
-   better security
-   simpler validation
-   lower maintenance
-   consistent tool behavior
-   predictable deployment

------------------------------------------------------------------------

# 4.7 Validation & Cross-Verification Harness

This is the system's trust boundary.

Nothing generated by Gemini should be shipped without validation.

## Check 1 --- Schema Fact Check

Every generated reference to:

-   table
-   column
-   relationship
-   KPI field

must be checked against the deterministic Schema Profile.

Invalid references are hard failures.

## Check 2 --- Agent/Skill Validation

Verify:

-   referenced tools exist
-   referenced skills exist
-   referenced agents exist
-   instructions don't contradict system constraints
-   outputs are grounded in available data

## Check 3 --- Self-Critique

Run a second Gemini review.

The reviewer should look specifically for:

-   hallucinated fields
-   unsupported KPIs
-   incorrect formulas
-   contradictory assumptions
-   unsafe workflows
-   unsupported claims

## Check 4 --- Plugin Schema Validation

Validate:

``` text
plugin.json
marketplace.json
SKILL.md
agent definitions
command definitions
MCP configuration
```

against the expected structures.

## Check 5 --- SQL Safety

Static-analyze generated queries.

Enforce:

-   read-only operations
-   no INSERT
-   no UPDATE
-   no DELETE
-   no DROP
-   no ALTER
-   table allow-list
-   column allow-list
-   row limits
-   query timeout

Use a SQL parser such as `sqlglot` where appropriate.

## Check 6 --- Dry Run

Actually execute generated queries against:

-   sandbox data
-   representative sample
-   or a live read-only connection

Verify:

-   query succeeds
-   referenced columns exist
-   result shape matches expectations
-   KPI calculation works

## Check 7 --- PII / Compliance Scan

Detect sensitive fields such as:

``` text
name
phone
email
DOB
Aadhaar
SSN
diagnosis
account_number
address
```

Ensure generated tools and artifacts do not expose sensitive data
unnecessarily.

------------------------------------------------------------------------

# 4.8 Packager

The Packager assembles:

``` text
.claude-plugin/
skills/
agents/
commands/
hooks/
config/
.mcp.json
README.md
```

It should:

-   validate structure
-   version the plugin
-   generate README
-   generate installation instructions
-   create ZIP package
-   optionally publish to marketplace repository

Output:

``` text
customer-mis-plugin-v1.0.0.zip
```

------------------------------------------------------------------------

# 4.9 Distribution Layer

The generated plugin should support three distribution modes.

## Mode 1 --- Local Development

``` bash
claude --plugin-dir ./mis-plugin
```

Useful for development and testing.

## Mode 2 --- Private Marketplace

For enterprise customers.

``` text
Your Git repository
        ↓
Private marketplace
        ↓
Customer
        ↓
Claude Code / Desktop
```

Useful when each customer receives a customized plugin.

## Mode 3 --- Public Marketplace

For a general-purpose MIS analytics plugin.

``` text
Public marketplace
        ↓
Claude Desktop / Claude Code
        ↓
User installs plugin
```

The public distribution path should be treated as a separate release
process with additional validation/review requirements.

------------------------------------------------------------------------

# 5. Claude Desktop User Experience

The intended user experience is:

``` text
Customer
   ↓
Claude Desktop
   ↓
Code
   ↓
Plugins
   ↓
Install MIS Analytics Plugin
   ↓
Plugin loads
   ↓
Skills + Agents + MCP become available
```

After installation, the user can ask natural-language questions such as:

``` text
"What was revenue last month?"

"Why did bookings decline?"

"Show me customer retention."

"Generate the monthly MIS report."

"Which locations are underperforming?"
```

The internal execution flow can be:

``` text
User Question
     ↓
Claude
     ↓
Relevant Skill
     ↓
Specialized Agent
     ↓
MCP describe_schema
     ↓
MCP run_safe_query
     ↓
Customer Database
     ↓
Validated Result
     ↓
Agent Analysis
     ↓
Claude Response
```

------------------------------------------------------------------------

# 6. Generic vs Industry-Specific Architecture

Use a two-layer design.

## Layer A --- Generic

Never changes substantially:

``` text
Plugin structure
Skill structure
Agent structure
MCP server
Validation harness
Packaging
Marketplace integration
```

## Layer B --- Industry Packs

Changes by industry:

``` text
Healthcare
EdTech
Finance
Retail
Generic
```

Industry Packs contain:

``` text
Entities
KPIs
Skills
Agents
Recipes
Artifacts
Guardrails
Examples
```

Therefore:

> Generic infrastructure + industry-specific configuration = scalable
> customization.

------------------------------------------------------------------------

# 7. End-to-End Example

Suppose a healthcare customer connects:

``` text
PostgreSQL
├── patients
├── bookings
├── providers
├── payments
└── lab_results
```

## Step 1 --- Ingest

Platform connects read-only.

## Step 2 --- Profile

Deterministic profiler discovers:

``` text
patients.patient_id
bookings.booking_id
bookings.patient_id
bookings.booking_date
payments.amount
lab_results.result
```

## Step 3 --- Gemini Exploration

Gemini identifies potential:

``` text
Revenue analysis
Booking retention
Provider performance
Patient cohorts
Lab-result analytics
```

## Step 4 --- Industry Classification

``` text
Healthcare / MIS
Confidence: 96%
```

## Step 5 --- Generation

The system generates:

``` text
skills/
├── booking-analysis/
├── revenue-analysis/
├── patient-retention/
└── provider-performance/

agents/
├── sql-specialist.md
├── healthcare-analyst.md
└── report-generator.md

config/
├── schema_bindings.json
├── kpi_defs.json
└── guardrails.json
```

## Step 6 --- Validation

Check:

``` text
✓ Tables exist
✓ Columns exist
✓ KPIs valid
✓ SQL read-only
✓ PII controls pass
✓ MCP calls work
✓ Skills valid
✓ Agents valid
✓ Plugin structure valid
```

## Step 7 --- Package

``` text
healthcare-mis-plugin-v1.0.0.zip
```

## Step 8 --- Install

Customer installs the plugin in Claude Code/Desktop.

## Step 9 --- Use

Customer asks:

``` text
"Why did our bookings decline in July?"
```

Claude can route the task through the appropriate skill/agent and use
the MCP data layer to answer from the customer's actual MIS.

------------------------------------------------------------------------

# 8. Gemini's Role

Gemini is the **generation engine**, not the runtime database execution
engine.

Recommended generation calls:

  -----------------------------------------------------------------------
  Call                    Purpose                 Output
  ----------------------- ----------------------- -----------------------
  Insight Explorer        Explore schema          Insights + candidate
                          semantics               KPIs

  Industry Matcher        Select Industry Pack    Ranked matches

  Skill Generator         Generate analytical     `SKILL.md`
                          playbooks               

  Agent Generator         Generate specialized    Agent definitions
                          workers                 

  MCP Config Generator    Bind generic MCP to     Config JSON
                          customer                

  Recipe Generator        Generate workflows      Recipe definitions

  Artifact Generator      Generate                Artifact specs
                          dashboards/charts       

  Self-Critique           Review generated output Validation findings
  -----------------------------------------------------------------------

The critical rule:

> Gemini can propose. Deterministic systems verify.

------------------------------------------------------------------------

# 9. Recommended Tech Stack

### Backend

``` text
Python
FastAPI
```

### Profiling

``` text
pandas
DuckDB
database information_schema
```

### LLM

``` text
Gemini API
```

### MCP

``` text
One generic Python MCP server
Configuration-driven
```

### Validation

``` text
JSON Schema
sqlglot
sandbox execution
PII detection
custom plugin validators
```

### Storage

``` text
PostgreSQL
Object Storage
```

### Packaging

``` text
ZIP
Git
Private marketplace
Public marketplace
```

### Frontend

Any suitable web frontend for:

``` text
data-source connection
schema preview
industry confirmation
generation progress
validation results
plugin download/install
```

------------------------------------------------------------------------

# 10. Security Architecture

Security must be designed into the system rather than added after
generation.

## Database

Use:

``` text
READ ONLY credentials
```

Never give generated agents write access unless there is an explicit,
separately reviewed requirement.

## Gemini

Send only:

``` text
structural metadata
safe representative samples
aggregated statistics
```

Avoid sending unnecessary PII/PHI.

## MCP

Enforce:

``` text
read-only
allow-lists
query limits
timeouts
result limits
schema validation
```

## Plugin

Generated instructions must not be able to bypass MCP safety controls.

## Validation

Every generated component must pass:

``` text
schema validation
security validation
PII validation
SQL validation
execution validation
```

------------------------------------------------------------------------

# 11. Key Risks

## 1. Generic tool generation

The largest technical risk is allowing Gemini to invent tools or queries
that do not match the customer's schema.

Mitigation:

``` text
Schema Profile
      ↓
Generation
      ↓
Fact Check
      ↓
Self-Critique
      ↓
Static Validation
      ↓
Dry Run
```

## 2. Messy MIS schemas

Real-world data may contain:

-   inconsistent names
-   missing primary keys
-   missing foreign keys
-   duplicated records
-   denormalized tables
-   mixed units
-   inconsistent categories

The profiling engine therefore needs significant engineering depth.

## 3. Industry ambiguity

A customer may span industries.

Support:

``` text
Primary Industry Pack
+
Secondary Industry Pack
```

instead of forcing one classification.

## 4. Data privacy

Healthcare and financial data may contain sensitive information.

The architecture must enforce the:

``` text
Schema Profile > raw data
```

boundary whenever possible.

## 5. Plugin ecosystem changes

The Claude plugin ecosystem may evolve.

Keep a thin adapter around:

``` text
Plugin manifest
Marketplace metadata
Plugin packaging
```

so changes in the target format don't require rewriting the generator.

## 6. Validation cost

Dry-running generated queries increases latency and infrastructure cost.

This should remain mandatory because trust is more important than
generation speed.

------------------------------------------------------------------------

# 12. Phased Roadmap

## Phase 0 --- Foundation

**2--3 weeks**

Build:

-   CSV ingestion
-   one database connector
-   deterministic profiler
-   Schema Profile JSON

No Gemini generation yet.

------------------------------------------------------------------------

## Phase 1 --- MVP

**3--4 weeks**

Single industry:

``` text
Healthcare / MIS
```

Build:

-   Industry Pack
-   generic MCP server
-   `describe_schema`
-   `run_safe_query`
-   `get_kpi`
-   basic Skills
-   basic Agents
-   Gemini generation
-   plugin packaging

Output:

``` text
Working installable plugin
```

------------------------------------------------------------------------

## Phase 2 --- Validation & Security

**\~2 weeks**

Add:

-   schema validation
-   SQL static analysis
-   dry-run
-   PII detection
-   agent validation
-   skill validation
-   plugin validation

Do not defer this phase.

------------------------------------------------------------------------

## Phase 3 --- Multi-Industry

**4--6 weeks**

Add:

``` text
EdTech
Finance
Retail / E-commerce
Generic Analytics
```

Add:

-   industry matching
-   blended packs
-   larger KPI library
-   more agent templates
-   more recipes

------------------------------------------------------------------------

## Phase 4 --- Distribution

**\~2 weeks**

Build:

``` text
private marketplace
plugin versioning
customer installation
ZIP distribution
Claude Desktop testing
public marketplace preparation
```

------------------------------------------------------------------------

## Phase 5 --- Feedback Loop

Track, with appropriate customer consent:

-   skill usage
-   agent usage
-   frequently requested KPIs
-   failed queries
-   validation failures
-   common user questions

Use this information to improve Industry Packs and generation quality.

------------------------------------------------------------------------

# 13. Recommended MVP Plugin

The first generated plugin should be deliberately small.

``` text
mis-analytics/
│
├── .claude-plugin/
│   └── plugin.json
│
├── skills/
│   ├── data-analysis/
│   │   └── SKILL.md
│   ├── kpi-analysis/
│   │   └── SKILL.md
│   └── report-generation/
│       └── SKILL.md
│
├── agents/
│   ├── sql-specialist.md
│   ├── data-analyst.md
│   └── report-generator.md
│
├── commands/
│   ├── analyze.md
│   ├── kpi.md
│   └── report.md
│
├── config/
│   ├── schema_bindings.json
│   ├── kpi_defs.json
│   └── guardrails.json
│
├── .mcp.json
├── README.md
└── LICENSE
```

The generic MCP server remains outside the generated plugin source and
is deployed/versioned as shared infrastructure where practical.

------------------------------------------------------------------------

# 14. Final Architecture

The complete system should look like this:

``` text
                         YOUR PLATFORM
                              │
             ┌────────────────┴────────────────┐
             │                                 │
       Customer Data                    Industry Packs
             │                                 │
             ▼                                 │
      Ingestion Layer                           │
             │                                 │
             ▼                                 │
     Deterministic Profiler                     │
             │                                 │
             ▼                                 │
       Schema Profile                           │
             │                                 │
             ├──────────────┐                  │
             │              ▼                  │
             │        Gemini Explorer          │
             │              │                  │
             └──────────────┴──────────────────┘
                            │
                            ▼
                  Industry Classification
                            │
                            ▼
                      Gemini Generator
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
       Skills            Agents          MCP Config
          │                 │                  │
          │                 │        ┌─────────┴─────────┐
          │                 │        │                   │
          │                 │ schema_bindings       kpi_defs
          │                 │        │                   │
          └─────────────────┼────────┴───────────────────┘
                            │
                            ▼
                    Recipes / Artifacts
                            │
                            ▼
                  Validation Harness
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
       Schema            SQL/PII           Dry Run
       Checks             Safety             Tests
          │                 │                 │
          └─────────────────┼─────────────────┘
                            │
                            ▼
                     Plugin Packager
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
            ZIP        Private Repo    Public Marketplace
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                    Claude Desktop
                       / Claude Code
                            │
                            ▼
                    Customer MIS Analysis
```

------------------------------------------------------------------------

# 15. Bottom Line

### Is it doable?

Yes.

The required primitives already exist:

-   plugin packaging
-   skills
-   subagents
-   MCP
-   marketplace distribution
-   deterministic data profiling
-   LLM generation
-   validation

The product is primarily an orchestration, generation, and verification
system.

### What makes it difficult?

Not generating the plugin.

The difficult part is making sure the generated plugin is **correct,
secure, schema-grounded, and reliable**.

The most important engineering asset is therefore the **Validation &
Cross-Verification Harness**.

### What is the moat?

Two major assets:

1.  **Industry Pack Library**
    -   entities
    -   KPIs
    -   formulas
    -   skills
    -   agents
    -   recipes
    -   guardrails
2.  **Verification Harness**
    -   schema verification
    -   SQL safety
    -   self-critique
    -   dry-run execution
    -   PII/compliance validation
    -   plugin validation

### Where should implementation start?

Start with:

``` text
Phase 0
  ↓
Deterministic Schema Profiler
  ↓
Generic MCP Server
  ↓
Healthcare/MIS Industry Pack
  ↓
Basic Skills + Agents
  ↓
Gemini Generator
  ↓
Validation Harness
  ↓
Installable Plugin
  ↓
Claude Desktop testing
```

The central design decision should remain:

> **Generate the plugin's customer-specific configuration and reasoning
> components; keep the MCP execution infrastructure generic, controlled,
> and reusable.**


---

# 16. Critical Distinction: Generator System vs Generated Plugin

The system being built is **not itself just one MIS plugin**.

It is a **Plugin Generator Platform** whose job is to create customer-specific Claude plugins.

This distinction must remain explicit throughout implementation.

```text
                    MIS-to-Plugin Generator Platform
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
              Customer MIS                 Industry Pack
                    │                           │
                    └─────────────┬─────────────┘
                                  ↓
                           Profiling Engine
                                  ↓
                         Gemini Generation
                                  ↓
                         Validation Harness
                                  ↓
                    Customer-Specific Plugin
                                  │
                 ┌────────────────┼────────────────┐
                 ↓                ↓                ↓
              Skills           Agents             MCP
                 │                │                │
                 └────────────────┼────────────────┘
                                  ↓
                         Installable Package
                                  ↓
                         Claude Desktop /
                           Claude Code
```

Therefore there are two separate architectural layers.

## Layer A — Generator Platform

This is the product we are building.

It contains:

```text
API / Backend
Data ingestion
Profiling engine
Industry packs
Gemini orchestration
Plugin generators
Validation harness
Plugin packager
Marketplace publishing
Customer metadata
```

## Layer B — Generated Plugin

This is the output of Layer A.

Each generated plugin contains only the components required by that customer:

```text
plugin/
├── .claude-plugin/
├── skills/
├── agents/
├── commands/
├── hooks/
├── config/
├── .mcp.json
└── README.md
```

### Never confuse these two layers

```text
Generator Platform
        │
        │ generates
        ▼
Generated Plugin
        │
        │ installed into
        ▼
Claude Desktop / Claude Code
```

This separation is fundamental to the product.

---

# 17. Recommended GitHub Architecture

The recommended implementation is a **GitHub-hosted monorepo for the generator platform and marketplace**, with generated customer plugins treated as build artifacts or separately published repositories depending on the deployment model.

Recommended repository:

```text
mis-to-claude-plugin-generator/
│
├── .github/
│   ├── workflows/
│   │   ├── ci.yml
│   │   ├── validate-marketplace.yml
│   │   ├── test-generator.yml
│   │   └── release.yml
│   └── ISSUE_TEMPLATE/
│
├── .claude-plugin/
│   └── marketplace.json
│
├── generator/
│   ├── api/
│   │   ├── routes/
│   │   ├── schemas/
│   │   └── dependencies/
│   │
│   ├── ingestion/
│   │   ├── csv.py
│   │   ├── excel.py
│   │   ├── postgres.py
│   │   └── base.py
│   │
│   ├── profiling/
│   │   ├── structural_profiler.py
│   │   ├── semantic_profiler.py
│   │   ├── relationship_detector.py
│   │   └── schema_profile.py
│   │
│   ├── classification/
│   │   ├── classifier.py
│   │   └── industry_matcher.py
│   │
│   ├── generation/
│   │   ├── gemini_client.py
│   │   ├── skill_generator.py
│   │   ├── agent_generator.py
│   │   ├── mcp_config_generator.py
│   │   ├── recipe_generator.py
│   │   └── artifact_generator.py
│   │
│   ├── validation/
│   │   ├── schema_validator.py
│   │   ├── plugin_validator.py
│   │   ├── skill_validator.py
│   │   ├── agent_validator.py
│   │   ├── sql_validator.py
│   │   ├── pii_scanner.py
│   │   ├── dry_run.py
│   │   └── cross_verifier.py
│   │
│   ├── packaging/
│   │   ├── plugin_builder.py
│   │   ├── marketplace_builder.py
│   │   └── versioning.py
│   │
│   └── publishing/
│       ├── github_publisher.py
│       └── marketplace_publisher.py
│
├── mcp-server/
│   ├── server.py
│   ├── tools/
│   │   ├── describe_schema.py
│   │   ├── get_data_profile.py
│   │   ├── run_safe_query.py
│   │   ├── get_kpi.py
│   │   └── search_records.py
│   ├── security/
│   │   ├── query_policy.py
│   │   ├── allowlist.py
│   │   └── pii_policy.py
│   └── runtime/
│       ├── config_loader.py
│       └── database.py
│
├── industry-packs/
│   ├── healthcare/
│   │   ├── entities.json
│   │   ├── kpis/
│   │   ├── skills/
│   │   ├── agents/
│   │   ├── recipes/
│   │   └── guardrails.json
│   │
│   ├── edtech/
│   ├── finance/
│   ├── retail/
│   └── generic/
│
├── plugin-templates/
│   ├── base/
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json
│   │   ├── skills/
│   │   ├── agents/
│   │   ├── commands/
│   │   ├── hooks/
│   │   ├── config/
│   │   ├── .mcp.json
│   │   └── README.md
│   │
│   └── enterprise/
│
├── generated/
│   ├── examples/
│   │   └── healthcare-demo/
│   │
│   └── .gitkeep
│
├── marketplace/
│   └── README.md
│
├── tests/
│   ├── ingestion/
│   ├── profiling/
│   ├── classification/
│   ├── generation/
│   ├── validation/
│   ├── mcp/
│   └── packaging/
│
├── docs/
│   ├── architecture.md
│   ├── generator-flow.md
│   ├── plugin-development.md
│   ├── marketplace.md
│   ├── security.md
│   └── customer-installation.md
│
├── scripts/
│   ├── generate_plugin.py
│   ├── validate_plugin.py
│   ├── build_marketplace.py
│   └── publish_plugin.py
│
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── README.md
└── LICENSE
```

---

# 18. Marketplace Repository Structure

Claude Code treats the marketplace as a **catalog** of plugins. The official documentation recommends GitHub as a hosting mechanism for marketplaces.

The marketplace root must contain:

```text
.claude-plugin/
└── marketplace.json
```

A simple marketplace repository can be:

```text
mis-plugins/
│
├── .claude-plugin/
│   └── marketplace.json
│
├── plugins/
│   ├── mis-analytics/
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json
│   │   ├── skills/
│   │   ├── agents/
│   │   ├── commands/
│   │   ├── config/
│   │   ├── .mcp.json
│   │   └── README.md
│   │
│   └── healthcare-mis/
│       ├── .claude-plugin/
│       │   └── plugin.json
│       ├── skills/
│       ├── agents/
│       ├── config/
│       └── .mcp.json
│
└── README.md
```

The marketplace manifest should resemble:

```json
{
  "name": "mis-plugins",
  "owner": {
    "name": "Your Organization"
  },
  "plugins": [
    {
      "name": "mis-analytics",
      "source": "./plugins/mis-analytics",
      "description": "AI-powered MIS analytics",
      "version": "1.0.0"
    }
  ]
}
```

The important rule is:

```text
marketplace.json
       ↓
lists plugins
       ↓
plugin source
       ↓
actual plugin directory
```

---

# 19. "Just Add the GitHub URL" Installation Model

The desired user experience is:

```text
User receives:

https://github.com/YOUR_ORG/mis-plugins
```

The user adds that marketplace to Claude Code:

```text
/plugin marketplace add YOUR_ORG/mis-plugins
```

or using the full Git URL:

```text
/plugin marketplace add https://github.com/YOUR_ORG/mis-plugins.git
```

Then:

```text
/plugin install mis-analytics@mis-plugins
```

For non-GitHub git hosting, the full repository URL can also be used.

Claude Code supports adding a marketplace from a GitHub repository, git URL, remote `marketplace.json` URL, or local path.

### Important URL distinction

There are two different URLs in the system:

```text
MARKETPLACE URL
    ↓
finds marketplace.json
    ↓
PLUGIN SOURCE
    ↓
downloads the actual plugin
```

For a GitHub-hosted marketplace, the recommended path is:

```text
GitHub repository
        ↓
marketplace.json
        ↓
plugins/<plugin-name>
```

This is preferable to simply hosting a raw `marketplace.json` file because relative plugin sources work naturally when the marketplace is cloned.

If distributing a marketplace via a direct URL to `marketplace.json`, the plugin source should use a remote source such as GitHub, git URL, npm, or archive rather than relying on a relative path.

---

# 20. Generated Plugin Publishing Models

The generator platform needs to support **three different publishing strategies**.

## Strategy A — Local / Downloaded Plugin

Best for development.

```text
Generator
   ↓
Generate plugin
   ↓
ZIP
   ↓
Customer installs locally
```

Example:

```text
customer-healthcare-mis-v1.0.0.zip
```

## Strategy B — Customer-Specific GitHub Repository

Best for enterprise customers.

```text
Generator
   ↓
Generate plugin
   ↓
Create repository
   ↓
Push plugin
   ↓
Customer adds repository/marketplace
```

Example:

```text
github.com/company/customer-acme-mis-plugin
```

This keeps each customer's generated plugin isolated.

## Strategy C — Central Marketplace

Best for reusable/general plugins.

```text
Generator
   ↓
Generate validated plugin
   ↓
Publish to marketplace repository
   ↓
Users install
```

This should only be used when the plugin is genuinely reusable across customers.

### Recommended rule

Do **not** place every customer's private MIS plugin into one public marketplace.

For customer-specific data access, prefer:

```text
Private customer repository
+
Private marketplace
```

For generic functionality, use:

```text
Public marketplace
```

---

# 21. Recommended Production Deployment

The generator itself should run as a backend service.

```text
                   Web UI
                     │
                     ▼
                FastAPI API
                     │
       ┌─────────────┼─────────────┐
       ↓             ↓             ↓
   Ingestion     Gemini        Industry Packs
       │             │             │
       └─────────────┼─────────────┘
                     ↓
              Generation Engine
                     ↓
             Validation Harness
                     ↓
               Plugin Builder
                     ↓
          ┌──────────┴──────────┐
          ↓                     ↓
      ZIP Output          GitHub Publisher
                                │
                                ▼
                         Marketplace Repo
```

The generated plugin then runs in the customer's Claude environment.

---

# 22. The Generic MCP Server Deployment Decision

The MCP server is different from the generator.

The generator creates:

```text
schema_bindings.json
kpi_defs.json
guardrails.json
```

The generic MCP runtime consumes them.

There are two recommended deployment patterns.

## Pattern A — MCP packaged with generated plugin

Use when the MCP server is lightweight and can run locally.

```text
Generated Plugin
├── .mcp.json
└── mcp-runtime/
```

## Pattern B — Remote/shared MCP service

Use for enterprise deployments.

```text
Claude
  ↓
Plugin
  ↓
.mcp.json
  ↓
Remote MCP service
  ↓
Customer database
```

The second pattern is preferable when:

- the database cannot be accessed locally
- credentials must remain server-side
- multiple users need the same data access
- auditing is required
- enterprise security controls are required

The exact deployment should be selected per customer.

---

# 23. GitHub Release Flow

Every generated plugin should go through:

```text
Generate
   ↓
Validate
   ↓
Dry Run
   ↓
Security Scan
   ↓
Package
   ↓
Version
   ↓
Git Commit
   ↓
Git Tag
   ↓
Publish
```

Example:

```text
v1.0.0
v1.1.0
v1.2.0
```

The plugin manifest should have a version.

When a plugin changes, bump its version so users can receive the update correctly.

---

# 24. CI/CD for the GitHub Repository

GitHub Actions should automatically validate every change.

Recommended pipeline:

```text
Pull Request
      ↓
Lint
      ↓
Unit Tests
      ↓
Industry Pack Validation
      ↓
Plugin Validation
      ↓
MCP Tests
      ↓
Security Tests
      ↓
Marketplace Validation
      ↓
Build
      ↓
Release
```

Example workflow files:

```text
.github/workflows/
├── ci.yml
├── validate-marketplace.yml
├── test-mcp.yml
└── release.yml
```

Before publishing a marketplace, run:

```bash
claude plugin validate .
```

Also validate each generated plugin individually:

```bash
claude plugin validate ./generated/customer-plugin
```

---

# 25. What the Public Repository Should Expose

The public GitHub repository should make it obvious that this is a **plugin generator**, not merely an MIS plugin.

The README should start with:

```text
MIS-to-Claude Plugin Generator

Generate customer-specific Claude Code/Desktop plugins
from MIS data sources using schema profiling, Gemini,
industry knowledge packs, MCP, specialized agents,
and automated validation.
```

Then show:

```text
Data Source
    ↓
Profile
    ↓
Industry Detection
    ↓
Generate
    ↓
Validate
    ↓
Claude Plugin
```

The repository should contain:

```text
README.md
architecture documentation
installation instructions
generator API documentation
plugin format documentation
example generated plugin
marketplace documentation
security model
contributing guide
```

---

# 26. Recommended Repository Separation at Scale

For the MVP, a monorepo is easiest.

```text
mis-to-claude-plugin-generator/
```

Later, when the product grows, separate:

```text
Repository 1
mis-plugin-generator
    ↓
Generator platform

Repository 2
mis-plugin-marketplace
    ↓
Public marketplace

Repository 3+
customer-<id>-mis-plugin
    ↓
Customer-specific generated plugin
```

This gives a clean separation:

```text
Generator Source
        ≠
Marketplace Catalog
        ≠
Customer Plugin
```

That separation becomes particularly important for private customer data and credentials.

---

# 27. Final Recommended Architecture

The complete product should now be understood as:

```text
                         ┌─────────────────────────┐
                         │ MIS-TO-PLUGIN GENERATOR │
                         │       PLATFORM          │
                         └────────────┬────────────┘
                                      │
        ┌─────────────────────────────┼──────────────────────────┐
        │                             │                          │
        ▼                             ▼                          ▼
 Data Ingestion                Industry Packs              Gemini
        │                             │                          │
        └─────────────────────────────┼──────────────────────────┘
                                      ↓
                              Schema Profile
                                      ↓
                              Plugin Generation
                                      │
             ┌────────────────────────┼──────────────────────┐
             │                        │                      │
             ▼                        ▼                      ▼
          Skills                   Agents              MCP Config
             │                        │                      │
             └────────────────────────┼──────────────────────┘
                                      ↓
                              Recipes / Artifacts
                                      ↓
                              Validation Harness
                                      ↓
                               Plugin Packager
                                      ↓
                        ┌─────────────┼──────────────┐
                        │             │              │
                        ▼             ▼              ▼
                       ZIP       GitHub Repo     Marketplace
                        │             │              │
                        └─────────────┼──────────────┘
                                      ↓
                              Claude Desktop
                              / Claude Code
```

### The three things we build

**1. Generator Platform**

```text
Creates plugins
```

**2. Generic MCP Runtime**

```text
Executes safe data operations
```

**3. Generated Plugin**

```text
Customer-specific Claude experience
```

### The public distribution path

```text
GitHub
  ↓
marketplace.json
  ↓
plugin source
  ↓
Claude Code / Claude Desktop
  ↓
Install
```

This is the architecture to use for implementation.

The user's requirement should therefore be treated as:

> **Build a system that automatically converts a customer's MIS/data source into a validated, versioned, installable Claude plugin, then optionally publishes that generated plugin through GitHub/private marketplaces/public marketplaces.**
