# What happens when you upload data

An end-to-end walkthrough of Data2plugin: every stage, every decision, what
is hardcoded and what is discovered, which KPIs get built and where they
come from.

Written to be honest about the limits, not just the design. Where something
is a heuristic, a hardcoded list, or a known weakness, it says so.

---

## 1. The one-paragraph version

You upload a file (or point at a database). The system measures your data
deterministically — no AI — producing a **profile**: every column's type,
how many distinct values it has, its range, its most common values, and
which columns join to which. An AI agent then reads that profile and works
out what the data *means in business terms*, asking you a handful of
questions about anything it genuinely cannot tell from the numbers. Your
answers plus the profile are used to bind your real column names onto a
library of **pre-written KPI templates**, which are compiled into real SQL,
executed against your actual data to prove they run, and packaged into a
Claude plugin you can install.

The critical design rule, which everything below serves:

> **A column's name is never evidence of what it means.**

`amount` might be revenue, a discount, or a refund. `score` is not money.
A column called `rashi` may be the revenue column. Names are hints to check
against measured values; they are never the decision.

---

## 2. The pipeline

```
   your file / database
            │
   ┌────────▼────────┐
   │ 1. INGEST       │  deterministic
   │ 2. PROFILE      │  deterministic + 3 AI agents
   │ 3. CLASSIFY     │  deterministic + AI evidence
   │ 4. BIND         │  AI, gate-verified
   │ 5. COMPILE_KPIS │  deterministic
   │ 6. GENERATE     │  AI (prose only)
   │ 7. VALIDATE     │  deterministic + 1 AI check
   │ 8. PACKAGE      │  deterministic
   └────────┬────────┘
            │
     installable plugin
```

Two stages can **pause and ask you something**: after CLASSIFY (business
questions) and during BIND (confirm a column mapping). Everything else runs
straight through.

---

## 3. Stage by stage

### 3.1 INGEST — read the data

**No AI.** Supported inputs:

| Input | Adapter |
|---|---|
| CSV, Excel, JSON, Parquet — single file, a folder, or a `.zip` | `ingestion/files.py` |
| SQLite database | `ingestion/sqlite.py` |
| PostgreSQL connection string | `ingestion/postgres.py` |
| Uploaded files loaded into a hosted warehouse | `ingestion/warehouse.py` |

Multiple files become multiple tables. Everything is queried through DuckDB,
read-only.

A live database connection string is **never stored**. It is swapped for a
`${VAR}` placeholder before the run record is written, so credentials never
reach the jobs database, logs, or API responses.

---

### 3.2 PROFILE — measure everything, then understand it

This is the most important stage and the one that costs the most.

#### 3.2a Structural profiling (no AI, ever)

`profiling/structural.py` computes, per column:

- data type
- null percentage
- **cardinality** (how many distinct values)
- **distinct ratio** (cardinality ÷ row count)
- min / max, and 25th/50th/75th percentiles for numbers
- up to **8 most common values** with their counts
- a structural **role**

Roles are decided **only from shape and real values**:

| Role | How it's decided |
|---|---|
| `IDENTIFIER` | Genuinely unique across every row, no nulls |
| `DATE` / `DATETIME` | The column type, **or** a real query proving every value parses — ISO first, then non-ISO formats like `DD-MM-YYYY` (see below) |
| `BOOLEAN_FLAG` | Boolean type, or a numeric with ≤2 distinct values |
| `NUMERIC` | A numeric type |
| `CATEGORICAL` | Text with few distinct values relative to row count |
| `FREE_TEXT` | Everything else |

**Deliberately absent: `CURRENCY` and `GEOGRAPHIC`.** They used to exist and
were assigned by matching the column name against
`amount|price|cost|revenue|fee|salary|total|balance|inr|usd`. That is how a
column called `total_score` became "currency" — and therefore summable — by
coincidence. Both are now semantic claims requiring evidence, not shapes.

It also detects relationships (foreign keys) by **sampling real values and
measuring overlap**, not by matching names.

**Dates stored as text in a non-ISO format** are handled explicitly, because
they are extremely common in Indian and European exports and the failure mode
is brutal: DuckDB's `CAST('02-05-1993' AS TIMESTAMP)` *raises* rather than
returning NULL, and every pack's trend KPI casts its date column — so one such
column used to take an entire build down at the very last check.

The real format is probed against the values, and day-first vs month-first is
settled by the data rather than a locale assumption: a first field above 12
can only be a day, so a single row like `31-12-1999` decides the column. The
format then travels through binding as a SQL expression —
`STRPTIME("from_date", '%d-%m-%Y')` — so every KPI works.

#### 3.2b The data map

`profiling/data_map.py` packages all of the above into a compact text block
handed to the AI. This is the single biggest performance lever in the system:
because every measured fact is in the prompt, the agents do not need to spend
tool calls rediscovering them.

Concretely — 300 to 950 tokens for a typical dataset, versus several thousand
for even one tool round-trip.

#### 3.2c Three AI agents run here

| Agent | Job |
|---|---|
| **Semantic profiling** (`agentic/data_agent.py`) | Proposes what each column means; suggests an industry |
| **Context discovery** (`agentic/agents/context_discovery.py`) | Works out what the data represents *as a business*: what one row is, what the entities are, what's ambiguous |
| **Understanding enrichment** (`understanding/agent.py`) | Follow-up on columns still unclear |

Semantic profiling is **one structured call over the data map**, not a tool
loop. It was measured at 39,109 tokens across 10 calls — 51% of an entire
build, more than every other component combined — to produce a sentence per
column and one industry guess. Both are judgements over facts the map already
states, so the loop was spending its budget re-reading its own prompt. It now
costs ~2,700 tokens in one call, with the tool agent kept as a fallback for
when that call yields nothing.

> **Known weakness.** These three still overlap in scope. Merging them
> outright remains the structural cleanup, though the cost argument for it is
> much weaker now.

#### 3.2d The questions you get asked

Three separate generators produce questions, capped at **8 total**:

| Source | Cap | Example |
|---|---|---|
| Business context (context discovery agent) | 4 | *"In 'sentiment', which of these values means the outcome was a success?"* |
| Value-set questions (`profiling/quality.py`) | 2 | *"Which `status` values mean the order completed?"* |
| Data-anomaly questions | 3 | *"85% of `agent_id` is one value — what does it mean?"* |

Which columns get asked about is chosen by **measured structure**, never by
name:

- **"What does one row represent?"** fires when a column repeats in a
  particular way — distinct ratio between 0.5 and 0.99, excluding dates and
  numbers, preferring a column that joins to another table. This is what
  catches *"5,530 records but only 2,260 unique phone numbers — is one row a
  lead, or a call?"*
- **"Which value means success?"** fires on enum-shaped columns: at most 8
  distinct values, **and** fewer distinct values than rows. The second half
  matters — a label is a label *because it repeats*, whereas a name or email
  is distinct on every row.
- **Casing problems** (`Guitar` vs `guitar`) are found with a real
  `SELECT DISTINCT` query, not a sample.

> **Known weakness.** `status` and `gender` on a table of 20 rows are both
> two-value label sets that repeat. Nothing distinguishes them without
> reading the column *name*, which this system will not do. So the offline
> fallback may ask about `gender`. The cost is one wasted question, never a
> wrong plugin — and the AI agent, which runs by default, picks the
> meaningful one.

Your answers flow into every later stage and into the shipped plugin.

---

### 3.3 CLASSIFY — pick an industry pack

**Mostly deterministic.** Your profile is scored against every pack:

| Signal | Weight |
|---|---|
| Table names match the pack's entity hints | 35% |
| Column names match the pack's column hints | 35% |
| Required structural roles are present | 20% |
| Table count in the expected range | 10% |

> **This is the most name-dependent part of the system**, and it is a
> deliberate exception: choosing which template library to try is low-risk
> and reversible (you can override it), unlike deciding that a column is
> money.

The context discovery agent's industry claim is folded in **as evidence** —
it can raise a pack's score and records why, but it cannot override the
deterministic ranking, and an abstained claim changes nothing.

If the top score is below **0.45**, the run pauses and asks you to confirm.

---

### 3.4 BIND — map your columns onto canonical roles

Each pack defines **canonical roles** — abstract concepts its KPIs are
written against. For example, edtech:

```
revenue_amount      the price of a course associated with an enrollment
transaction_status  the enrollment lifecycle state
transaction_date    the date of enrollment
student_ref         the student identifier
course_ref          the course identifier
score               the student's performance score
```

Binding decides: *which of your real columns is `revenue_amount`?*

**Tier 1 — one AI call over the data map.** The map already contains every
fact needed, so this is a judgement, not an investigation. One structured
call, no tools, ~5,000 tokens, returning a proposal per role with cited
evidence.

**Every proposal is then gate-verified** (`validation/gates.py`):

| Gate | Checks |
|---|---|
| V1 | The cited evidence actually exists — you cannot invent a number |
| V2 | The claimed unit is consistent with the measured values |
| V3 | Claimed aggregations are mathematically sound — **never `SUM` on a score, rating, percentage or identifier** |

A claim that fails is rejected and retried once with the failure reason fed
back.

**Tier 2 — deterministic name scorer.** Token overlap between the role name
and your column names. Only ever produces a *low-confidence* candidate that
must be confirmed.

**Tier 3 — a tool-using agent**, and only when the data map flagged genuine
ambiguity. If nothing is ambiguous, a role the single call declined is
almost always a concept your data does not contain (asking for `student_ref`
in a table of sales leads), and no amount of querying invents the column.

**Anything still unresolved, or resolved with low confidence, pauses and
asks you** — it is never guessed silently.

---

### 3.5 COMPILE_KPIS — where the KPIs actually come from

**No AI.** There are three sources, and this is the part most worth
understanding.

#### Source 1: the industry pack's hand-written KPIs (hardcoded)

Each pack ships hand-authored KPI templates as JSON. **This is the main
hardcoded content in the system**, and it is deliberate: these are reviewed,
tested SQL, not generated text.

| Pack | KPIs |
|---|---|
| **edtech** (6) | `total_enrollments`, `completion_rate`, `dropout_rate`, `average_score`, `enrollments_by_course`, `monthly_enrollment_trend` |
| **healthcare-diagnostics** (7) | `total_revenue`, `cancellation_rate`, `monthly_revenue_trend`, `repeat_customer_rate`, `number_of_repeat_customers`, `bookings_by_location`, `revenue_by_partner_and_product` |
| **retail-ecommerce** (7) | `total_revenue`, `average_order_value`, `cancellation_rate`, `return_rate`, `monthly_revenue_trend`, `orders_by_payment_channel`, `revenue_by_location` |
| **finance** (5) | `total_transaction_volume`, `average_transaction_amount`, `failed_transaction_rate`, `monthly_transaction_trend`, `volume_by_transaction_type` |
| **generic-analytics** (5) | `total_records`, `count_by_category`, `sum_measure`, `average_measure`, `trend_by_month` |

A template looks like this — note it references **roles**, not column names:

```json
{
  "id": "completion_rate",
  "label": "Completion Rate",
  "requires": { "filters": ["transaction_status"] },
  "sql": "SELECT ROUND(100.0 * SUM(CASE WHEN {{transaction_status}} IN {{completed_values}} THEN 1 ELSE 0 END) / COUNT(*), 2) AS completion_percent FROM {{fact}}",
  "unit": "percent",
  "assertions": ["completion_percent >= 0", "completion_percent <= 100"]
}
```

Compilation substitutes your real column names for `{{transaction_status}}`
and `{{fact}}`. **A KPI whose required roles didn't bind is skipped with a
written reason** — it is never shipped half-working.

`{{completed_values}}` comes from the pack's `value_set_hints` (e.g.
`["completed", "active", "passed"]`) — **or from your answer**, if you were
asked which statuses count as completed. Your answer wins.

#### Source 2: auto-generated metrics (deterministic)

`compiler/metric_generator.py` builds a flexible metric per numeric column,
so the plugin can answer questions the pack's fixed KPIs don't cover.

**Additivity is default-deny.** A numeric column gets:

- `total` (SUM), `average`, `min`, `max` — **only if a gate-verified claim
  explicitly says the column is additive**
- otherwise just `average`, `min`, `max`, `median`

This is why `score` never gets a "total". Being numeric is not evidence that
summing is meaningful.

Columns claimed as identifiers are excluded entirely — that is what stopped
the system generating "average student_id".

#### Source 3: AI-proposed KPIs (optional)

A few extra candidates specific to your data. **Every one goes through the
exact same compile gate as a hand-written pack KPI** — a bad proposal lands
in the skipped list with a reason. It is not a new trust surface.

---

### 3.6 GENERATE — write the plugin's prose

**AI, but only for text.** Skills, the agent definition, commands, and the
README. Each is one single-shot call, not an agent.

It cannot invent a KPI: the catalog is already fixed and verified. If the
generated text mentions a metric that doesn't exist, `self_critique` catches
it in the next stage.

---

### 3.7 VALIDATE — 10 checks, run against your real data

| Check | What it does |
|---|---|
| `fact_check` | Every KPI the pack marks required actually compiled |
| `sql_safety` | No `SELECT *`, no denied column in any clause |
| `binding_plausibility` | Compares each binding against the real distribution — this is the check that catches a test score bound to revenue |
| `dry_run` | **Every KPI is executed against your data** and its assertions must hold |
| `pii_scan` | No denied column appears in SQL or shipped text |
| `plugin_spec` | Manifest and layout are valid |
| `cli_validate` | `claude plugin validate` passes |
| `mcp_smoke` | The MCP server starts and answers a real tool call |
| `hooks_smoke` | The session hook runs |
| `self_critique` | An AI reviews the generated text for claims the data doesn't support |

A hard failure stops the build. **A KPI that cannot execute never ships.**

---

### 3.8 PACKAGE — what you get

```
your-plugin/
├── .claude-plugin/plugin.json
├── .mcp.json
├── agents/            AI-written analyst agent
├── commands/          slash commands
├── skills/            AI-written analysis skills
├── config/
│   ├── kpi_defs.json          compiled SQL for every verified KPI
│   ├── metric_defs.json       flexible auto-generated metrics
│   ├── schema_bindings.json   role → your column
│   ├── schema_summary.json    profile + your answers + business context
│   └── data-understanding.json
├── data/              your data (or warehouse credentials)
├── hooks/             session-start context
└── mcp_server/        the query runtime
```

The MCP server exposes: `get_kpi`, `query_metric`, `list_metrics`,
`describe_schema`, `run_safe_query`, `search_records`, `get_data_profile`,
`render_chart`.

**What ships from the business context, and what doesn't:**

| Ships | Withheld |
|---|---|
| Record grain ("one row is one call attempt") | Unconfirmed hypotheses |
| Your confirmed answers | Unresolved questions |
| Business objective, entity keys | Any column flagged as PII |

Hypotheses are withheld because a guess reads exactly like a fact once it is
inside a prompt.

---

## 4. What is hardcoded — the honest list

### Legitimately hardcoded (reviewed content, not heuristics)

| What | Where |
|---|---|
| ~30 KPI SQL templates across 5 packs | `industry-packs/*/kpis/*.json` |
| Canonical role names and descriptions | `industry-packs/*/pack.json` |
| Default value-set hints (`completed`, `cancelled`, …) | pack `value_set_hints` — **overridden by your answers** |
| Guardrail notes and query limits | pack `guardrails` |
| Skill / agent / command templates | pack `*_templates` |

### Hardcoded name lists that still make decisions

These are the remaining name-based heuristics. Each is listed with why it is
tolerated:

| Heuristic | Where | Why it's acceptable |
|---|---|---|
| Industry classification hints (`enrollment`, `student`, `order`, …) | `pack.json.signature` | Picks a *template library*, is scored not decisive, and you can override it |
| Binding name scorer (token overlap) | `binding/scorer.py` | Only ever produces a low-confidence candidate that must be confirmed |
| PII name patterns (`phone`, `email`, `aadhaar`, …) | `profiling/structural.py` | **Off by default** (`FORGE_ENABLE_PII_PROTECTION`). When on, causes irreversible deletion — so it is opt-in |
| Non-production value hints (`test`, `staging`, `dummy`, …) | context discovery | Matched against **values, not names**, and only ever raises a *question* — nothing is auto-excluded |

### Removed, and why

| Removed | Why |
|---|---|
| `CURRENCY` role from a name regex | Made `total_score` summable as money |
| `GEOGRAPHIC` role from names | Same class of error |
| Industry guess from keywords (`slug == "edtech"`) | Worked for exactly one industry, and made its own test tautological |
| Name-gated "percent" fingerprint | A 0–100 number is indistinguishable from age or score |
| Name-based date detection | Replaced with a real `TRY_CAST` query |

---

## 5. What it costs

Measured end-to-end on `bookings.csv` with the healthcare pack, cold caches,
every call attributed to its exact call site:

| Component | Tokens | Calls | Share |
|---|---|---|---|
| Context discovery | 11,545 | 3 | 30% |
| Generation | 10,306 | 5 | 27% |
| Self-critique | 6,791 | 2 | 18% |
| Binding | 5,334 | 1 | 14% |
| Semantic profiling | 2,729 | 1 | 7% |
| Profiling (questions, value sets) | 2,017 | 4 | 5% |
| **Total** | **38,722** | **16** | |

That is down from **76,150 tokens across 25 calls** for the same build.

Every run reports its own total in the UI, broken down per component, and it
is stored in the database per run.

**What made it fast:**

1. Every measured fact goes into the prompt, so agents don't spend calls
   rediscovering them.
2. Binding is one structured call, not a tool loop. The tool loop reached its
   step limit without proposing anything in 2 of 3 measured runs; the single
   call resolved 9/9 roles three times out of three.
3. Semantic profiling is also one structured call - it was 51% of a whole
   build on its own, spending ten tool-calling rounds to write a sentence per
   column that the profile already supported.
4. Semantic profiling is cached across a pause, instead of being re-derived
   at full price every time you answer a question.

The one remaining lever is the AI KPI proposers (~7,200 tokens, ~19%), which
suggest *extra* KPIs on top of each pack's hand-written catalog. Turning them
off is a product decision - giving something up - rather than removing waste.

---

## 6. Known limitations

- **Three overlapping agents in PROFILE.** Still overlapping in scope, though
  two of the three are now single calls, so the cost argument for merging them
  is much weaker than the tidiness one.
- **Pack mismatch is not detected.** Point a lead-generation dataset at the
  edtech pack and it will classify as edtech (it *is* a school) while most
  canonical roles fail to bind, because the pack is written around
  enrollments rather than call outcomes. The system reports the unresolved
  roles honestly but does not suggest a better-fitting pack.
- **Structurally identical columns are indistinguishable offline** — see the
  `status` vs `gender` note above.
- **`finance` has no test dataset**, so it has no evaluation benchmark.
- **PII protection is off by default** while in testing. Turn it on before
  real customer data.

---

## 7. The rules everything obeys

1. A column name is never evidence of what it means.
2. Anything unproven is asked about, not guessed.
3. Every KPI is executed against real data before it ships.
4. Nothing may be summed unless something verified says it is additive.
5. A fabricated finding is indistinguishable from a real one downstream —
   so nothing is fabricated. Unknown fields stay empty.
