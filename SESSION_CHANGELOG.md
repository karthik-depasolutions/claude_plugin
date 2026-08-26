# Data2Plugin — Session Changes & Implementation Summary

---

## 1. Multi-Tenant RBAC & Admin Access Control

### 1.1 Tenant Isolation for Runs & Plugins
- **Scoped API Queries**: `GET /runs` supports `scope=mine` (default) and `scope=all` (admins only).
- **Data Protection**: Regular users can only view, stream, download, and manage their own runs; touching another tenant's run returns `403`/`404`.
- **`runs.tenant_id`** is a real indexed column (migration `0003`), not a field buried in the JSON blob, so the filter happens in SQL.

### 1.2 Admin Control Banner & Scope Switcher
- [`RunsDashboard.tsx`](apps/web/src/components/RunsDashboard.tsx): admin-only header banner, an **All Platform Runs / My Runs Only** toggle, and a `👤 <tenant_email>` tag on cards in the all-runs view.

---

## 2. Business Context Discovery Agent

Implements [`DATA2PLUGIN_CONTEXT_DISCOVERY_AGENT_IMPLEMENTATION.md`](DATA2PLUGIN_CONTEXT_DISCOVERY_AGENT_IMPLEMENTATION.md).

### 2.1 Pydantic v2 Structured Schemas
[`agentic/schemas/business_context.py`](packages/forge-core/src/forge_core/agentic/schemas/business_context.py): `Evidence`, `Hypothesis`, `BusinessQuestion`, `BusinessAnswer`, `EntityDefinition`, `LifecycleEvent`, `BusinessProcessDefinition`, `BusinessKPI`, `BusinessContext`.

### 2.2 Investigation Tools
[`agentic/context_tools.py`](packages/forge-core/src/forge_core/agentic/context_tools.py): `inspect_schema`, `inspect_column`, `get_duplicate_profile`, `detect_inconsistent_categories`, `sample_rows`, `run_safe_duckdb_query`, plus `submit_context_findings` as the terminating tool.

- **AST SQL rewriting** maps logical table names onto physical refs via `sqlglot.to_table()`.
- **Table allowlisting**: every referenced table must belong to this tenant's dataset (CTE aliases excepted). Previously an unrecognised name was passed through un-rewritten to DuckDB, which would resolve another attached database or an internal catalog view — a tenant-isolation hole.
- Denied/PII columns are blocked from inspection and from queries.

### 2.3 Selection by measured evidence, not column names

The first cut of this agent chose which columns to investigate with English name-substring lists (`"id"`/`"phone"`/`"outcome"`/`"status"`/`"agent"`) and guessed the industry with a hardcoded `slug == "edtech"` keyword check. That is the exact failure mode the agent exists to replace, and it only worked on datasets whose columns happen to be named in English. It is gone. Selection now comes from measured properties:

| Finding | Signal now used |
|---|---|
| Record grain ambiguity | A column that repeats a few times each — "many rows describe the same real-world thing" (thresholds in §2.6) |
| Entity key | Measured uniqueness (`ColumnRole.IDENTIFIER` from the structural profile) |
| Status/outcome fields | Enum shape: a short value set that actually repeats (§2.6) |
| Casing variants | A real `SELECT DISTINCT` query, not the truncated `sample_values` sample |
| Non-production rows | Hints matched against **values**, never column names — and only ever raising a question, never auto-excluding |

Covered by `test_discovery_is_industry_and_language_agnostic`, which runs the same dataset with Turkish column names (`kayit`/`musteri`/`durum`) and asserts identical structural findings.

### 2.4 Honest abstention instead of fabrication

`_build_business_context_model` previously filled `business_objective`, `business_process` (a hardcoded Created→Contacted→Converted funnel), `candidate_kpis`, `desired_questions`, `success_definition`, and a snap-to-0.95 confidence with plausible-sounding constants regardless of the data. Downstream, a fabricated finding is indistinguishable from a discovered one. These are now `None`/`[]` unless actually evidenced, agent-claimed, or user-confirmed:

- **`domain`** is only set from the agent's own claim; with no LLM available it stays `None` rather than keyword-matching a pack slug.
- **`is_additive`** defaults to `False` for every measure. Additivity is a gate-verified semantic claim (see the default-deny SUM in `compiler/metric_generator.py`); being numeric is not evidence of it.
- **`overall_confidence`** tracks the share of asked questions actually answered, and is halved when the agent errored.
- **Readiness gate** requires all critical questions resolved *and* no agent error — a failed semantic pass means we know less, not more.

### 2.5 Interactive questions
Questions are grounded in real observed values, phrased for a non-technical owner, and offer a free-text write-in alongside the suggested chips. They surface at the `PROFILE` human-review gate.

### 2.6 Question budget

The first structural cut over-fired badly: 12 questions on a 17-column table, including *"which customer_name means success?"*, and it picked `booking_date` as the entity. Every candidate is still chosen by measured structure, but with real discriminators:

| Guard | Why |
|---|---|
| Entity candidate excludes DATE/DATETIME/NUMERIC | A repeating date is ordinary; a repeating measure means nothing |
| Entity candidate needs `distinct_ratio >= 0.5` | A low ratio is a *dimension* (city, tier), not an entity key |
| A join-backed column wins | `orders.customer_id` beats `orders.shipping_city` on real FK evidence |
| Enum needs `cardinality <= 8` **and** `cardinality < row_count` | A label is a label *because it repeats*; a name or email is distinct on every row. Row-count-relative by construction, unlike a fixed ratio — which a column with casing variants defeats by inflating its own cardinality |
| ≤3 enum questions per table, ≤8 per run, one `critical` per table | Spec §13/§14. Marking every enum critical meant the readiness gate could never close |
| Data-quality detection is **not** capped | Finding `'Guitar'`/`'guitar'` is cheap and always worth reporting; only *questions* are rationed |

Result: healthcare 12→4, retail 14→8, edtech 8→3, and names/emails are gone. PII columns stay eligible for the *grain* question (which cites only counts, never values) because the spec's headline example is itself a phone number; they remain excluded from enum questions, which put real values on screen.

**Known limit, deliberately not papered over:** `bookings.status` and `bookings.gender` are both two-value label sets repeating over 20 rows — structurally identical. Nothing separates them without reading the column *name*, so the deterministic floor picks by a stable rule and may ask about `gender`. The worst case is one wasted question, never a wrong plugin, and the LLM agent (mandatory on real runs) picks the meaningful column. Documented in the fixtures' `_README.md`.

---

## 3. The §22 handoff — BusinessContext is now consumed, not just stored

`BusinessContext` was produced, persisted, and read by **nothing**: its questions were never shown to anyone, its domain claim was discarded, and classification/binding/generation each re-derived what the agent had already investigated. Four wires fix that.

### 3.1 One payload, four consumers
`BusinessContext.to_handoff()` is merged into the run's shared `data_context` — the payload `DataReview.to_context()` already threads into binding, KPI proposal, generation and packaging. One merge reaches every consumer instead of a new argument on four signatures.

`render_data_context` renders it into prompts under **separate headings** for confirmed facts, unconfirmed hypotheses ("do not state as fact"), and open questions ("do not invent an answer"). Collapsing those is the "pretend an inference is confirmed fact" failure the spec forbids — a prompt that can't tell *"the owner told us X"* from *"we suspect X"* treats both as settled.

### 3.2 Classification consumes the agent's domain
`classify(profile, packs, business_context=...)` raises a pack's score to the agent's confidence when it names one, recording the reason as a matched signal. Deliberately `max`, not a sum, and evidence rather than an override: the deterministic matcher still ranks every pack, a domain that scores near-zero structurally still loses, and a weak top match still pauses for confirmation. An abstained `domain=None` changes nothing.

### 3.3 Questions reach the human
`merge_context_questions_into_review()` puts the agent's open questions on the same review page as the deterministic ones — ordered by impact, capped at 4, deduped against the `biz:{column}` questions `profiling/quality.py` already asks, and idempotent across a resume. Routing them through `DataReview` means answers return via the existing `to_context().notes` path with no further wiring.

Gated on the agentic path actually being live: offline, discovery degrades to a structural pass whose questions overlap the existing ones — and since adding questions is what makes a run *pause*, merging unconditionally would stop every `--no-llm`/CI run for input it can't act on.

### 3.4 Answers feed back
`answers_to_business_answers()` turns the review page's replies back into the agent's own type, so a resumed run continues discovery from what the customer said (spec §13's adaptive loop) instead of re-asking. Answered questions become confirmed facts and raise `overall_confidence`.

### 3.5 Shown to the user
`BusinessContextPanel.tsx` renders grain, entities, and three visually distinct groups — confirmed / best guess / still unclear. A reader can tell what was established from what is still a guess, which is the entire point of the agent.

### 3.6 It ships with the plugin

The build UI isn't the only audience. Record grain rides in `config/schema_summary.json` and the SessionStart hook renders it into **every** session of the generated plugin, because not knowing that one row is an interaction rather than a customer produces a wrong answer on the first `GROUP BY` anyone writes. A repeating entity gets an explicit instruction: *"count distinct values, not rows"*.

The packager ships an **explicit allowlist**, extended deliberately rather than passing the payload through:

- **Ships:** grain, business objective, primary entities, owner-confirmed facts.
- **Never ships:** hypotheses and open questions — a hypothesis reads exactly like a fact once it's in a prompt, and nobody in a plugin session can resolve an open question.
- **Gated:** an entity naming a denied column is dropped through the same PII gate the quality findings pass. Shipping the *name* of a column whose values were physically deleted is the exact mismatch that once let a plugin's `pii_scan` pass while it still leaked.

Verified by running the generated hook as a real subprocess against a real `schema_summary.json` — reimplementing its rendering in the test would only have tested the copy.

**Verified live** on `sparda_leads.csv`: the agent found grain *"One call interaction / stage update per lead contact attempt"*, classification recorded `Context Discovery Agent identified this domain (95% confidence)` as a matched signal, and 4 questions reached the user — the `phone_number` grain question (the spec's headline case), a multi-choice success definition, `spardha-staging`/`test_bot` filtering found in the *values*, and `Guitar` vs `guitar`.

---

## 4. Cross-industry golden evaluation

`fixtures/evaluation/context_discovery/` now holds a benchmark per dataset — healthcare, retail, edtech (leads), edtech (SQLite), generic — discovered at collection time, so adding a directory adds coverage with no test change. The previous fixtures named tables that don't exist in the real datasets (`encounters`, `visit_status`) and pack slugs that aren't real (`healthcare`, `retail`); nothing read them.

Each fixture splits `deterministic` (asserted every run) from `semantic` (needs a live agent). That split matters: the original edtech fixture asserted `domain == "edtech"` and passed **only** because of a hardcoded `slug == "edtech"` keyword fallback — it measured the fallback, not the agent.

36 parametrized checks assert, for every industry: entity keys from measured uniqueness, the question budget and that each question carries its evidence, quality-issue detection, no measure assumed additive, no fabricated business facts, a populated handoff, and questions reaching the review page.

---

## 5. Token Accounting — "what did this plugin cost to build?"

Every LLM call in a run is now measured, attributed, persisted, and shown to the user.

### 3.1 Capture
- **`UsageTracker`** ([`llm/provider.py`](packages/forge-core/src/forge_core/llm/provider.py)) — `GeminiProvider` records `usage_metadata` after every call. Previously the entire `LLMProvider` path (profiling, generation, self-critique) reported **zero** tokens; only the agents were instrumented, and only into event payloads that were never totalled.
- Thinking/reasoning tokens are counted **once**, as part of billed output, with a separate `thinking_tokens` breakdown — matching how Gemini bills them.
- `CassetteProvider` delegates `drain_usage()`, so the wrapper the orchestrator actually holds doesn't report every run as free.
- Extraction never raises: a blocked response or an older SDK degrades to zero, never fails the run that produced it.

### 3.2 Attribution
- **`TokenUsage`** on `RunRecord` accumulates per component: `profiling`, `generation`, `critique`, `context_discovery`, `data_understanding`, `binding`.
- The orchestrator drains providers at each stage boundary, so spend lands against the stage that incurred it.
- The context discovery agent's `on_stats` callback was never wired — its tokens were silently lost. Now billed.
- Accumulates across resume, so the figure is the whole build, not the last pass.

### 3.3 Persistence & display
- Migration **`0005`** promotes `input_tokens`, `output_tokens`, `total_tokens` (indexed), `llm_calls` into real columns, so spend is queryable per tenant without deserializing blobs. The per-component breakdown stays in `record_json`.
- `RunSummary` exposes `total_tokens`/`llm_calls`; `RunDetail` carries the full `token_usage`.
- **`TokenUsagePanel.tsx`** shows the total, input/output split, call count, and a per-step bar breakdown; run cards show a compact token count.
- Usage is attached to **pause** events too — a run parked on a human has usually already paid for the expensive agent passes, and showing nothing until it finally succeeds would hide the bulk of the cost.

**Verified live** on `edtech.sqlite`: 82,588 tokens / 42 calls, breakdown summing exactly to the total across all five components.

---

## 6. Cost and re-ask fixes (from a real run)

A live build surfaced four problems: 13 questions, the same questions handed
back after answering them, and 73k+ tokens without finishing.

### 6.1 The review page came back after being answered
A resumed run replays from ingest and appends to the same event list, so the
first pass's `Awaiting customer input` event was still in history. The UI
searched all of history for it, so `needsAnswers` stayed permanently true —
which both re-rendered the answered review panel **and** suppressed the
binding gate that was actually blocking the run. The owner answered 13
questions and was handed the same 13 back, with no way forward.

Both pause sites log immediately before returning, so the *later* of the two
is the live pause; anything earlier is settled history. `Wizard.tsx` now
resolves the live pause instead of any historical one.

### 6.2 The most expensive agent re-ran on every resume
Only `data_review` and `business_context` were cached on the record.
Semantic profiling — the priciest agent in the pipeline — re-ran from
scratch on every pause, re-deriving a near-identical answer at full price:
**~31k of one build's 73k tokens.** Now cached as `RunRecord.semantic_profile`.
Structural profiling still re-runs; it's deterministic and cheap.

### 6.3 Two agents had never run at all
`build_investigation_tools()` never accepted `evidence_sink`, but two callers
passed it. Both wrap construction in a bare `except Exception`, so the
`TypeError` was swallowed and the **batched binding agent** and the
**understanding enrichment agent** silently returned nothing on every run —
while still logging an invocation with zero steps and zero tokens (visible
in the UI, and easy to read as "the agent looked and found nothing").

All binding intelligence was therefore coming from the legacy per-role tier-4
agent. The parameter now exists and records one line per *successful* tool
call — a refused call proves nothing and must not become citable evidence.
`verify_column_claim`'s V1 check reads this log, so claims made from a tool
call are now verifiable at all.

### 6.4 Binding asked the same agent the same question, per role
Tier 4 runs a full agent session **per role**, after the batched pass already
investigated every role with strictly more context (data map, real values,
all roles at once, gate-verified). Re-asking bought nothing and dominated
cost: 42k tokens over ~20 extra calls on a 6-role pack. It is now skipped
when the batched pass ran — if that pass declined a role, the honest outcome
is needs-confirmation, not three more guesses.

### 6.5 One budget for the review page
Three generators fed it (anomaly, value-set, context discovery), each with
its own cap and none aware of the others: 3 + 5 + 1 + 4 = **13**. Now a
single `MAX_REVIEW_QUESTIONS = 8` ceiling filled by priority — questions that
resolve *meaning* outrank ones annotating a finding that gets reported
anyway, and the open-ended "anything else?" drops first. Upstream caps also
came down (anomaly 5→3, value-set 3→2) so the trim saves tokens rather than
discarding questions already paid for.

### Measured, same dataset, cold caches

| | Before | After |
|---|---|---|
| Questions asked | 13 | **8** |
| Re-asks after answering | yes | **no** |
| Tokens (run + resume) | 99,220 | **67,338** |
| Binding LLM calls | 21 | **8** |
| Batched binding agent | dead (0 steps) | **4 steps, 3 tool calls** |

---

## 7. Non-ISO dates no longer fail the whole build

A real customer run died at `dry_run` with:

```
Conversion Error: invalid timestamp field format: "02-05-1993",
expected format is (YYYY-MM-DD hh:mm[:ss[.uuuuuu]])
```

`from_date` was text in **DD-MM-YYYY** — the norm in Indian and European
exports. The severity comes from a DuckDB detail: `CAST('02-05-1993' AS
TIMESTAMP)` **raises** rather than returning NULL, and every pack's trend KPI
casts its date column. So one such column took down an entire build, *after*
the full agent spend, instead of degrading to a skipped KPI.

Profiling now probes the real format against the values. Day-first vs
month-first is decided by the data, not by locale assumptions: a first field
above 12 can only be a day, so one unambiguous row (`31-12-1999`) settles the
whole column. The format rides through binding as a SQL *expression*:

```sql
STRFTIME(CAST(STRPTIME("from_date", '%d-%m-%Y') AS TIMESTAMP), '%Y-%m')
```

Three places build time-bucket SQL independently — compiled KPIs, generated
metrics, and the shipped MCP runtime — so all three go through one helper.
The runtime ships standalone and cannot import `forge_core`, so its copy is
duplicated deliberately, with a test asserting the two have not drifted: a
metric must not validate at build time and raise at query time.

Set once in `_attach_temporal_expressions` rather than at each of the five
tiers that build a `ColumnBinding` — a tier added later would silently omit
it, and the failure is severe and remote from its cause.

---

## 8. Halving the cost

Every LLM call was instrumented to its exact call site rather than guessed at.
On `bookings.csv` + healthcare, cold caches:

| Component | Before | After |
|---|---|---|
| **data_understanding** | **39,109 / 10 calls** | **2,729 / 1 call** |
| context_discovery | 11,822 / 3 | 11,545 / 3 |
| generation | 11,049 / 5 | 10,306 / 5 |
| critique | 7,124 / 2 | 6,791 / 2 |
| binding | 5,135 / 1 | 5,334 / 1 |
| profiling | 1,911 / 4 | 2,017 / 4 |
| **Total** | **76,150 / 25** | **38,722 / 16** |

**One agent was 51% of the entire run.** Its whole output is a sentence per
column plus one industry guess — both judgements over facts the data map
already states, not investigations. It was spending ten tool-calling rounds
re-reading what its own prompt had handed it.

Replaced with a single structured call, same as binding, with the tool agent
kept as a fallback when the call yields nothing. **49% cheaper and nine fewer
network round-trips.**

Combined with §6.4's binding fix (86k → ~5k), a build that measured 329,928
tokens should now land in the 30-45k range.

### Remaining profile

No third dominant item is hiding; the distribution is flat now. The one
remaining lever is `kpi_proposer` + `metric_proposer` (~7.2k, ~19%), which
produce *optional* extra KPI suggestions on top of each pack's hand-written
catalog. Making them opt-in is a product decision, not waste removal, so it
was left alone.

---

## 9. Bug fixes

- **Test suite made hermetic.** Agents build `ChatGoogleGenerativeAI` directly from `GEMINI_API_KEY` rather than going through `LLMProvider`, so `FORGE_LLM_CASSETTE_MODE` never gated them. Once the agent path became mandatory, a developer's local `.env` turned the suite into real, billed, minutes-long network calls — the run **hung past 15 minutes**. Both conftests now blank the key (set-empty, not delete: provider lookups call `load_dotenv()`, which repopulates a *deleted* var but leaves a present-but-empty one alone). Genuinely-live tests opt in with `@pytest.mark.live_llm`. Runtime: **>15 min hang → 3m26s**.
- **Numeric columns destroyed on small tables.** `_demote_tied_identifiers` demoted every tie-broken column to `FREE_TEXT`. That reasoning holds for text, but a numeric column reaches `NUMERIC` on dtype alone, before any cardinality rule — so a score or amount that was unique by coincidence of row count could never be selected as a measure again. Demotion now respects dtype.
- **`download` and `publish` returned 404 after any new `PACKAGE` event.** Several consumers located a stage's payload by taking the *last* event of that stage. Added `_last_event_with(record, stage, key)` so the lookup selects on the payload it actually needs; the same latent fragility was hit three times during this work (`plugin_dir`, the pause flags, the e2e assertions) and is why token usage is attached to existing events rather than emitted as its own.
- **CLI crashed on Windows** with `UnicodeEncodeError` — a non-cp1252 emoji in a progress line took the whole run down. `stdout` is now reconfigured to UTF-8 with `errors="replace"`; progress text comes from all over the pipeline (increasingly from model output), so sanitising call sites individually is a losing game.
- **Dev-database schema drift.** `create_all` never alters an existing table, and the compensating "self-healing" block was a hand-maintained ALTER list that had already drifted from Alembic. It now diffs the live table against the ORM metadata, so adding a column to a model is all that's required. Covered by `test_db_schema_healing.py`.
- **Removed a stale duplicate package.** `agentic/tools/` shadowed `agentic/tools.py`; it was unimported dead code carrying an older copy of `context_tools.py` (missing the denied-column check and the AST rewrite), and would have broken three live imports the moment it gained an `__init__.py`.
- **Deleted a decorative LangGraph stub.** `build_context_discovery_graph()` had no-op nodes, hardcoded `ready=True`, and was never invoked by anything.

---

## 10. Package & Schema Reorganization

- Legacy single-file schemas moved into [`agentic/schemas/agent_contracts.py`](packages/forge-core/src/forge_core/agentic/schemas/agent_contracts.py).
- [`agentic/schemas/__init__.py`](packages/forge-core/src/forge_core/agentic/schemas/__init__.py) exposes all agent contracts.
- Duplicate file shadowing removed so Python 3.13+ test discovery works.

---

## 11. Verification

| Suite | Collected | Result |
|---|---|---|
| `packages/forge-core/tests` | 362 | 351 passed, 11 skipped |
| `packages/mcp-runtime/tests` | 43 | 43 passed |
| `tests/e2e` | 3 | 3 passed |
| `apps/api/tests` | 25 | 25 passed |
| **Total** | **433** | **422 passed, 11 skipped, 0 failed** (6m52s) |
| `apps/web` — `tsc --noEmit` + `npm run build` | | clean |
| `alembic upgrade head` | | at `0005` |

The 11 skips are live-Postgres tests (no local server). Two long-standing e2e failures were also fixed: `location` cannot bind to `city`/`shipping_city` deterministically now that `GEOGRAPHIC` is no longer a name-derived role, so those tests supply an explicit override standing in for human confirmation, the same device `test_validation_harness.py` already uses.

### Tests added
- `test_context_discovery_golden.py` — 36 parametrized checks across 5 industry benchmarks (entity keys, question budget + evidence, quality issues, non-additive measures, no fabricated facts, populated handoff, questions reaching the review page).
- `test_business_context_shipping.py` — 4 tests: grain reaches `schema_summary.json`, a denied column never ships as an entity identifier, uncertain material is withheld, and the generated SessionStart hook (run as a real subprocess) tells the analyst to count distinct values rather than rows.
- `test_business_context_handoff.py` — 14 tests pinning the §22 wires: facts vs hypotheses kept apart, abstention, prompt rendering with certainty preserved, classification evidence (and that an abstained domain changes nothing), question surfacing/capping/dedupe/idempotence, and the answer round-trip.
- `test_token_usage.py` — 6 tests: accumulate/drain, thinking-token double-count, malformed responses, cross-component totals, resume, cassette delegation.
- `test_db_schema_healing.py` — 3 tests: legacy DB gains ORM columns, existing rows backfilled not dropped, idempotent across restarts.
- `test_context_discovery.py` — industry/language agnosticism, offline domain abstention, non-additive measures, SQL table allowlisting.

### Known limitations
- **Two structurally identical enums are indistinguishable offline.** `bookings.status` vs `bookings.gender` — see §2.6. The LLM agent resolves it; the deterministic floor may ask about the wrong one.
- **`retail`'s `customers` table** has no genuinely repeating entity, so the grain question falls to `city` on that 10-row fixture. Benign (the question is answerable), and an artifact of fixture size rather than the rule.
- **`finance` has no benchmark** — there's an `industry-packs/finance` pack but no matching dataset in `fixtures/datasets/`. The other five packs are covered.
- **Semantic expectations need a live key.** Each fixture's `semantic` block (domain, business process) is recorded but only assertable with `GEMINI_API_KEY` set; the offline suite pins the abstention contract instead.
- **`BusinessContext` informs but never overrides.** Classification treats the agent's domain as evidence (`max`, not a sum), and the handoff reaches binding/generation as prompt context rather than as hard constraints. That is deliberate — the deterministic matcher and the binding gates stay the source of truth — but it does mean a correct agent finding can still lose to a strong structural signal.
- **No `StateGraph`.** Spec §19/§20 sketch a LangGraph state machine with a `ContextDiscoveryState` TypedDict. The bounded workflow it asks for is real — deterministic evidence pass → `create_agent` session with a hard `recursion_limit` → assembly — but it isn't expressed as a graph. The version that shipped one had nodes returning `{}` and a hardcoded `ready=True`, and nothing ever invoked it; that decorative graph and its orphaned state type were deleted rather than left to look like a feature.
