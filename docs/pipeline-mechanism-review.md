# Plugin generation pipeline — step by step, with mechanism

Written for a technical review: for every step in turning a customer's raw data into an
installed Claude Code/Desktop plugin, this says exactly what runs — deterministic code, a
single-shot LLM call, or a tool-using agent — and where the code lives. One call to
`run_pipeline` ([orchestrator.py](../packages/forge-core/src/forge_core/orchestrator.py))
does all of it; the CLI and the web API both just call it.

## Legend

| Mechanism | Meaning |
|---|---|
| **Deterministic** | Plain code. No model involved. Same input → same output, always. |
| **LLM (single-shot)** | One prompt in, one completion out. No tools, no multi-step reasoning. |
| **Agent** | LangChain, tool-using, multi-step (can query real data, search the web, reason across several turns before answering). |
| **Agent (opt-in)** | Same as above, but only runs when `--agent` / `use_agent=True` is explicitly passed. Off by default. |

**The one rule that holds everywhere an LLM or agent appears:** it *proposes*, and
deterministic code *decides*. Nothing an LLM or agent outputs — a column name, a KPI, a
paragraph of prose — ships until deterministic code has independently checked it against
the real schema, real SQL grammar, or a real query execution. See "Safety architecture"
at the bottom for the specifics.

---

## Pipeline overview

```
1. INGEST          Deterministic
2. PROFILE
   a. Structural   Deterministic
   b. Semantic     LLM (single-shot)          — or Agent (opt-in)
   c. Data quality Deterministic (5 checks)
   d. DQ questions LLM (single-shot)          — phrasing only, findings are deterministic
   ── PAUSE: ambiguous industry and/or open questions go back to the human ──
3. CLASSIFY        Deterministic              (+ agent's industry guess shown, advisory)
4. BIND             per canonical role, 3 tiers, deterministic-first:
   a. Scorer       Deterministic              — resolves most roles
   b. Proposer     LLM (single-shot)          — only for roles (a) couldn't resolve
   c. Bind agent   Agent (opt-in)             — only for roles (a) and (b) both couldn't resolve
5. COMPILE_KPIS
   a. Pack KPIs    Deterministic              — template render + SQL validate
   b. Extra KPIs   LLM (single-shot, opt-in)  — proposals go through the same validate gate as (a)
6. GENERATE
   a. Skill        Deterministic + LLM intro paragraph
   b. Subagent     Deterministic + LLM system prompt
   c. Commands     Deterministic + LLM intro line
   d. Hooks        Deterministic only — never LLM-authored
7. PACKAGE         Deterministic
8. VALIDATE         8 checks, 7 deterministic + 1 LLM:
   a-g. checks 1-7 Deterministic
   h. self_critique LLM (single-shot)         — a second model reviews the first model's prose
```

---

## 1. Ingest — Deterministic

Reads a CSV/Excel/JSON/Parquet file or directory, a SQLite file, or a live `postgresql://`
connection, and registers every table as a DuckDB view so every later stage queries through
one uniform SQL surface regardless of source format.

- Code: `forge_core/ingestion/` (`files.py`, `sqlite.py`, `postgres.py`, `warehouse.py`)
- No model involved anywhere in this stage.

## 2. Profile

### 2a. Structural profiling — Deterministic

Per column: data type, null %, cardinality, a rule-based role guess (identifier, currency,
date, email, ...), PII-likelihood (name-pattern based), plus cross-table relationship/grain
detection via name similarity and value-overlap sampling.

- Code: `profiling/structural.py`, `profiling/relationships.py`, `profiling/grain.py`

### 2b. Semantic profiling — LLM (single-shot), or Agent if `--agent`

**Default:** one prompt containing deterministic structural facts + a capped, PII-redacted
sample of real rows. The model proposes: a plain-English meaning for ambiguous columns
(each with a 0–1 confidence score), 2–4 candidate analytical insights, data-quality flags,
and which tables look like the core business entities. Every claim is evidence-attached and
never trusted blindly — the fact-check validation step (8g below) independently re-verifies
anything downstream code actually relies on.
- Code: `profiling/semantic.py::run_semantic_profile`

**With `--agent`:** replaced by a genuine tool-using agent that can dig deeper before
answering — it can preview real column values (refuses PII-flagged columns outright) and
search the web for unfamiliar business terminology, instead of guessing from a name and a
handful of sample rows alone. It also proposes a best-fit industry guess with reasoning,
grounded in real data it looked at (the deterministic classifier in step 3 never sees
individual cell values, only column names/structure). A genuinely unclear column is
*expected* to get a low confidence score — that's the signal that turns it into a
clarification question for the human, not a modeling failure.
- Code: `agentic/data_agent.py`, tools in `agentic/tools.py`

### 2c. Data-quality analysis — Deterministic

Five checks run as batched DuckDB `histogram()` queries (one scan per table): a dominant
value (≥60% of rows), a high-null column, inconsistent casing/spelling of the same value,
a column mixing text labels with numeric-looking values, and a column with only one
distinct value. Plus (from 2b) any column the model flagged as low-confidence becomes a
sixth kind of finding (`unclear_meaning`) through the identical code path.
- Code: `profiling/quality.py::analyze_quality`

### 2d. Data-quality questions — LLM (single-shot)

Turns the *deterministic* findings above into plain-English questions for the human — the
model only ever writes question **text**; the finding IDs it can attach a question to are
fixed and server-assigned beforehand, so it cannot invent a new finding or attach a question
to something that doesn't exist. Falls back to a fixed template per finding type if no LLM
is configured or the call fails.
- Code: `profiling/quality.py::generate_questions`

**Pause here:** if the industry classification (step 3) is ambiguous, or there are
unanswered questions from 2d, the run stops and waits for a human. A clean, confidently-
classified dataset never pauses.

## 3. Classify — Deterministic

Scores every industry pack's hand-curated signature (table-name hints, column-name hints,
required canonical-role categories, expected table-count range) against the profile using
token-overlap matching. **No model call in this stage at all** — purely name/structure
matching. If `--agent` was used, step 2b's industry guess (grounded in real data values, not
just names) is shown alongside this ranking as an advisory hint — it never auto-selects a
pack; a human still confirms.
- Code: `classification/matcher.py`

## 4. Bind — three tiers, deterministic-first

For every canonical role a KPI needs (e.g. "revenue_amount"), resolved in this order —
each tier is only tried if the one before it failed:

**4a. Deterministic scorer** — token overlap between the role's name/hints and real column
names, plus type compatibility. Resolves the large majority of roles with no model
involved.
- Code: `binding/scorer.py`

**4b. Single-shot LLM proposer** — only for roles the scorer couldn't confidently resolve.
One prompt: "which of these real columns best fits this concept?" Its answer is checked
against the real column list before being trusted at all — it cannot bind to a column that
doesn't exist.
- Code: `binding/llm_proposer.py`

**4c. Bind agent (`--agent` only)** — last resort, for roles both (a) and (b) failed on. A
tool-using agent that can preview real column values and search terminology before
answering, with the same "must name a real column or say null" invariant as (b).
- Code: `agentic/binding_agent.py`

Every accepted binding also carries which tier produced it (`source: "deterministic" |
"llm_proposed" | "agent_proposed" | "human_override"`), so it's always auditable after the
fact which decisions were model-assisted.

## 5. Compile KPIs

**5a. Pack KPIs — Deterministic.** Each industry pack hand-defines KPIs as a SQL *template*
using canonical-role placeholders (e.g. `{{revenue_amount}}`), never physical column names.
Compiling one is pure string substitution against this customer's real bindings, then
parsed and validated with `sqlglot` (must be a single `SELECT`/`WITH`, nothing else). A KPI
that can't be satisfied by this customer's schema is skipped, never faked.
- Code: `compiler/kpi_compiler.py`

**5b. Extra KPIs (`--agent` only) — LLM (single-shot).** Given the pack's *existing* KPI
catalog (to avoid duplicates), this customer's actually-bound roles/columns, the resolved
category values, and anything the business owner said during the review pause, one call
proposes up to 5 additional KPI candidates specific to this customer's data. **Every
candidate is compiled through the exact same gate as 5a** — same template substitution,
same `sqlglot` validation. A bad proposal simply fails to compile and is recorded with a
reason; it is never a different trust boundary than a hand-authored KPI.
- Code: `compiler/kpi_proposer.py`

## 6. Generate plugin content

Four artifacts, each mixing deterministic scaffolding with a small amount of LLM-authored
prose:

| Artifact | Deterministic part | LLM part |
|---|---|---|
| Skill (`SKILL.md`) | KPI catalog, guardrail text, tool usage instructions | One framing intro paragraph |
| Subagent | Tool allow-list, name, model — fixed | The system prompt |
| Slash commands | The KPI-running steps | One intro line per command |
| Hooks (`hooks.json`) | Everything | **Nothing — never LLM-authored**, since hook config controls what auto-runs every session |
- Code: `generation/skills.py`, `generation/agents.py`, `generation/commands.py`,
  `generation/hooks.py`

Every fact any of these states (a KPI id, a label, a guardrail) is substituted in from
real, already-validated pipeline output — a missing or misbehaving model can only make the
prose blander, never factually wrong about what's available.

## 7. Package — Deterministic

Serializes the manifest, MCP server config, `schema_bindings.json`, `kpi_defs.json`,
`schema_summary.json`, and every generated file to disk. A second PII gate here re-filters
anything from the data-quality review against denied columns before it can reach the
packaged plugin.

**Important:** the MCP tools themselves (`describe_schema`, `get_kpi`, `list_kpis`,
`run_safe_query`, `search_records`, `get_data_profile`, `render_chart`) are **not**
generated per customer — they're fixed, shared Python code (the `mis-mcp-runtime` package),
identical for every install. What's customer-specific is only the deterministic *config*
those tools run against.
- Code: `packaging/`

## 8. Validate — 8 checks, 7 deterministic + 1 LLM

| # | Check | Mechanism | What it verifies |
|---|---|---|---|
| 1 | `fact_check` | Deterministic | Every bound column re-verified against the real schema |
| 2 | `sql_safety` | Deterministic | No `SELECT *`, no writes, table allow-list, denied columns absent |
| 3 | `dry_run` | Deterministic (executes real queries) | Every compiled KPI actually runs against real data; assertions checked |
| 4 | `pii_scan` | Deterministic | Denied columns absent from SQL and generated prose |
| 5 | `plugin_spec` | Deterministic | Manifest/frontmatter/hooks structurally valid |
| 6 | `cli_validate` | Deterministic | Shells out to the real `claude plugin validate --strict` |
| 7 | `mcp_smoke` | Deterministic | Every tool actually called over a live MCP session |
| 8 | `self_critique` | **LLM (single-shot)** | A second model reviews the first model's generated prose for hallucinated numbers/columns not grounded in real KPI ids/tool names |

A hard failure on any check blocks the run from being marked successful — the plugin stays
on disk for inspection, it never reaches a download/install/publish path. A soft warning
(e.g. `self_critique` flagging something ambiguous) doesn't block, but is visible in the
report.
- Code: `validation/`

---

## Every AI touchpoint, in one list

| # | What | Mechanism | Always runs? | Code |
|---|---|---|---|---|
| 1 | Semantic profiling | LLM single-shot | If LLM enabled | `profiling/semantic.py` |
| 2 | Data-quality question phrasing | LLM single-shot | If LLM enabled + `--review` | `profiling/quality.py` |
| 3 | Binding fallback proposer | LLM single-shot | Only for unresolved roles | `binding/llm_proposer.py` |
| 4 | Skill intro paragraph | LLM single-shot | If LLM enabled | `generation/skills.py` |
| 5 | Subagent system prompt | LLM single-shot | If LLM enabled | `generation/agents.py` |
| 6 | Command intro line | LLM single-shot | If LLM enabled | `generation/commands.py` |
| 7 | Self-critique review | LLM single-shot | If LLM enabled | `validation/self_critique.py` |
| 8 | Data-understanding agent | **Agent**, replaces #1 | Only with `--agent` | `agentic/data_agent.py` |
| 9 | Binding agent | **Agent**, last-resort tier | Only with `--agent`, only for roles #3 also failed | `agentic/binding_agent.py` |
| 10 | Extra-KPI proposer | LLM single-shot | Only with `--agent` | `compiler/kpi_proposer.py` |

Everything else in the pipeline — ingest, structural profiling, classification, the
deterministic binding scorer, KPI compilation, packaging, and 7 of the 8 validation checks
— is plain deterministic code with no model in the loop at all.

## Safety architecture — why untrusted output never ships unchecked

The same pattern repeats at every AI touchpoint above: **propose, then independently
verify** — never propose and trust.

- A column binding an LLM or agent proposes is checked against the real column list before
  it's accepted (steps 4b/4c). It can say "I don't know," but it can't invent a column.
- A KPI an LLM proposes (step 5b) is compiled through the identical `sqlglot`-validated
  pipeline a hand-authored KPI goes through (step 5a) — same SQL-injection-proof template
  substitution, same single-statement requirement, same runtime execution check (`dry_run`).
- Generated prose (step 6) never states a fact that wasn't substituted in from already-
  validated pipeline output — an LLM here can only choose *words*, never *numbers* or *ids*.
- `self_critique` (step 8h) is itself just one more opinion — it's not the only check; it
  runs alongside 7 deterministic ones, and a hard failure from *any* of the 8 blocks the
  ship, not just this one.
- PII is gated twice independently: once during profiling (before any of it could reach a
  prompt) and again at packaging time against the final denied-columns list.

## Things worth a second opinion

Flagging honestly, not just the parts that went well:

1. **Agent-proposed KPIs are uncurated.** No human industry expert reviewed them the way a
   pack's own KPIs are. The compile/validate/dry-run gates catch anything structurally or
   semantically broken (this was caught live during testing — see commit history), but a
   technically-valid, business-questionable KPI could still ship. Mitigated by labeling
   (`source: "agent_proposed"`) so it's never presented as pack-authored.
2. **Cost/latency with `--agent` on.** The data-understanding agent's tool use and the
   extra KPI-proposal call add real LLM calls beyond the default path — still cheap in
   absolute terms (~2¢/plugin without `--agent`, a few times that with it), but a genuine
   step up from the deterministic-first default.
3. **Resume cost.** Re-running a paused pipeline re-executes ingest/profile/classify from
   scratch (a documented, pre-existing constraint) — with `--agent`, that means paying for
   the agentic profiling pass twice on any run that pauses and resumes. Not solved yet.
4. **Non-determinism.** LLM/agent output isn't perfectly reproducible run to run (low
   temperature, not zero). This is why the data-quality review and its findings are
   computed exactly once per run and cached — never recomputed on resume — so a pause/answer
   cycle can't have its questions silently change underneath the user.
