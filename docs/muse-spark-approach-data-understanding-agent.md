# Muse Spark — Research Approach & Data-Understanding Agent Plan

> Goal: the agent that makes Data2plugin's generated plugin **truly data-aware** — so every skill, tool description, hook, and KPI analysis is customized to the customer's actual data, not generic boilerplate.
>
> This is my independent research path and build plan. The existing Ox Alpha plan (`docs/data-understanding-agent-plan.md`) covers the same problem from a different angle; this document complements it rather than replacing it.

---

## 1. How I Researched (Methodology)

I follow **evidence before synthesis**: read the local repo first, then search externally, then synthesize.

### 1.1 Local codebase audit (what I actually read)

| Area | Files inspected | Purpose |
|------|-----------------|---------|
| Overall architecture | `docs/architecture.md`, `docs/generator-flow.md`, `docs/source-architecture-doc.md`, `docs/plugin-format.md`, `README.md` | Confirm pipeline stages, trust boundary, plugin spec constraints |
| Deterministic profiling | `packages/forge-core/src/forge_core/profiling/structural.py:1-173` | How roles, cardinality, PII hints are inferred without LLM |
| LLM semantic layer | `packages/forge-core/src/forge_core/profiling/semantic.py:1-141` | Prompt shape, redaction boundary, single-shot vs agent path |
| DataMap (agent grounding) | `packages/forge-core/src/forge_core/profiling/data_map.py:1-212`, `models/data_map.py:1-77` | Percentiles, top-values, fingerprint, ambiguous-column work queue, prompt budgeting |
| Grain inference | `profiling/grain.py:1-41` | Single-column PK detection — where it already works and where it stops |
| Schema contract | `models/schema_profile.py:1-154` | StructuralProfile vs SemanticProfile separation |
| Agentic layer | `agentic/data_agent.py:1-160` (column-meaning + industry guess), `agentic/data_understanding_agent.py:1-80` (binding claims), `agentic/investigation_tools.py:1-447` (6 tools: inspect/compare/check_relationship/test_value_set/aggregate/sample_rows), `agentic/tools.py`, `agentic/memory.py` | Tool allowlisting, recursion limits, evidence sinking |
| Generation (what "customized" currently means) | `generation/skills.py:1-104`, `generation/agents.py:1-73`, `models/plugin_spec.py` | KPI-catalog + intro paragraph is the only per-data text today |
| Industry knowledge | `industry-packs/edtech/kpis/*.json`, `industry-packs/*/signatures.json`, `packaging/plugin_builder.py` | Canonical-role → SQL template → sqlglot validation pattern |

### 1.2 External research (targeted searches, then deep reads)

Search clusters I ran:

1. **Claude plugin system** — official `code.claude.com/docs/en/plugins-reference`, `docs/en/agent-sdk/plugins`, `docs/third-party/claude-desktop/extensions`; community synthesis (hidekazu-konishi, alexop.dev, alexcloudstar) for marketplace/plugin.json/hook/MCP wiring.
2. **Agent Skills / SKILL.md spec** — `agentskills.io/specification`, Anthropic engineering posts, `anthropics/skills` repo, progressive-disclosure pattern (metadata ~100 tokens always-loaded → SKILL.md body <5k tokens on activation → references/scripts on demand).
3. **Text-to-SQL & semantic layers** — dbt Labs 2026 benchmark (`semantic-layer-vs-text-to-sql-2026`), Atlan 2026 coverage, Denodo 2024 benchmark; production guides (AI Workflow Lab) on schema linking as retrieval + three-stage query validation.
4. **Semantic profiling / PII / quality** — Datachecks Discovery pattern (deterministic stats → LLM interpretation → hybrid regex+LLM PII), LLMDap (VLDB 2025) pipeline, general deterministic profiling literature.

Key takeaway before planning: **the hardest part is not generation — it is grounding**. Every external source converges on "compute facts deterministically, let the LLM interpret, verify mechanically, abstain when unsure."

---

## 2. What the Current System Already Gets Right

*9-stage pipeline is cleanly typed* (`orchestrator.py:run_pipeline` orchestrates `models/*` outputs). Skipped here for brevity — see `docs/architecture.md:10-27`.

Key strengths to preserve:

- **Separation of facts vs claims**: `StructuralProfile` (always, no LLM) vs `SemanticProfile` (LLM claims, never trusted blindly) — `models/schema_profile.py:62-143`. Validation harness fact-checks every later reference against StructuralProfile.
- **One generic MCP runtime + compiled KPI SQL**: LLM proposes bindings, `compiler/*` renders SQL, `sqlglot` validates, harness dry-runs. LLM never ships raw executable code — `docs/source-architecture-doc.md:62-84`.
- **DataMap as agent affordance**: percentiles, top-values (PII-redacted), format fingerprints (`currency|iso_date|email|uuid|enum`), and `ambiguous_columns` work queue — `profiling/data_map.py:48-62`, `models/data_map.py:40-44`. This is the pattern that makes a tool-using agent affordable (no per-column round trips for obvious columns).
- **Tool allowlisting**: agent supplies params, never SQL text; every identifier checked against real schema, literals are bound params, denied columns refused inside the tool — `agentic/investigation_tools.py:37-62` and `agentic/tools.py`.
- **Bounded sessions**: `MAX_AGENT_STEPS` (15 for binding agent, 30 for data agent), `AgentCallRecorder` token/step accounting, graceful degradation to `([], None)` on failure — `agentic/data_agent.py:36-144`.

---

## 3. External Insights That Shape the Plan

| # | Finding | Source | Implication for this agent |
|---|---------|--------|---------------------------|
| 1 | Semantic-layer-mediated agents reach **98–100%** accuracy on modeled data vs **50–62%** raw text-to-SQL on the same questions (Sonnet 4.6 / GPT-5.3, same model, same data). | dbt Labs 2026 benchmark | Agent's primary output must be **semantic-substrate** (roles, grains, joins, vocabularies) that the compiler turns into verified SQL — not ad-hoc query answering. Your compiler-stays-authoritative split is the right bet. |
| 2 | Schema linking at enterprise scale must be **retrieval** (hybrid BM25 + dense over a column index), not full-schema in prompt. | AI Workflow Lab production guide; Denodo 2024 | Agent must triage: profile everything cheaply, reason deeply only about candidates. Already embodied in `render_prompt(char_budget=30_000)` — extend it. |
| 3 | Progressive disclosure: L1 metadata → L2 SKILL.md body → L3 references/scripts, flat 3-layer max, tell the agent *when* to load each file. | agentskills.io spec; Anthropic engineering | Generated skills embed **pointers** into a per-customer understanding artifact, not dumps. One generic skill body + `config/data-understanding.json` reference = non-generic behavior at low token cost. |
| 4 | Determination-first, LLM-second + hybrid PII (regex + LLM semantic) + human-in-the-loop for low-confidence claims. | Datachecks Discovery; LLMDap; industry practice | Do not replace `structural.py`; extend it. LLM only interprets ambiguous residue. PII belongs in understanding, not just packaging gate. |
| 5 | Multi-agent specialization (schema understanding / SQL gen / validation as separate narrow agents) beats single mega-prompt on accuracy and debuggability. | Text-to-SQL survey (arXiv 2410.01066); agentic BI literature | Decompose understanding into **specialists communicating through a typed artifact**, not one chatty session. |

---

## 4. Gap Analysis — What "Deep Understanding" Needs vs What Exists

| Capability | Exists | Gap to close for *customized* skills/tools |
|------------|--------|-------------------------------------------|
| **Structure** (tables, columns, types, row counts) | Full via ingestion + `structural.py:_profile_column` | None — solid foundation |
| **Distribution** (cardinality, null%, min/max, distinct_ratio, sample_values) | Per column — `structural.py:85-126` | Missing: **percentiles for numerics** (added in DataMap but not in base ColumnProfile), full **value vocabularies** for categoricals, **temporal span** for dates, **shape descriptors** (skew, outliers) |
| **Top-values / format fingerprints** | In DataMap only — `data_map.py:84-118` (top 8, currency/uuid/enum/iso_date/email) | Not in base profile; not used by generators; no currency-symbol detection, no unit inference, no phone/Aadhaar/PAN patterns |
| **Grain** (one row = one *what*?) | Single-column PK exact match — `grain.py:14-22` | No composite-key search, no near-unique detection, no bridge-table recognition |
| **Relationships** | Partial: `relationships.py` + verified edges in DataMap | No orphan-rate surfacing to generators; no fan-out warning; no cross-source (multi-file) key discovery |
| **Column semantics** | 6-entry cap single-shot + opt-in agent (`semantic.py:92-94`, `data_agent.py:58-66` capped at ~3 samples/row) | No per-column **business glossary** grounded in values; no vocabulary frequencies; confidence is LLM-self-reported, not gated |
| **Business questions / KPI candidates** | `candidate_insights` 2–4 entries, advisory | No ranking by data support; no SQL sketch validated through compiler; generators ignore them |
| **Quality** | `quality.py:build_data_review` flags some issues | Not mapped to **KPI impact** ("12% cancelled — revenue SUM must filter"); not fed into hooks |
| **PII / sensitivity** | `structural.py:30-38` name-pattern + `validation/pii.py` gate | Name-pattern only; no hybrid semantic pass; not available to generators for redaction decisions |
| **Customized plugin prose** | `generation/skills.py:59-93` (KPI catalog + 2-sentence intro), `generation/agents.py:30-49` (tool list + KPI ids) | No per-data narratives (grain explanation, status vocabularies, mandatory filters, temporal coverage); skill is **generic + catalog**, not data-aware |

**Single sentence gap**: understanding is *computed but not narrated* — the generators have no rich artifact to quote, so they cannot make skills/tools feel customer-specific.

---

## 5. Design Principles

1. **Facts are deterministic; meaning is interpreted.** Keep `structural.py` as ground truth. LLM only narrates ambiguous columns and proposes hypotheses — every claim carries `{method, evidence, confidence}`.
2. **Abstain > hallucinate.** Unresolvable → `open_question` for human review (wizard pause), never a confident wrong mapping. This matches the existing binding philosophy (`data_understanding_agent.py:62-70`).
3. **One artifact, many consumers.** A single versioned **DataUnderstanding** JSON feeds skill prose, agent system prompts, hook predicates, and wizard UI — no free-text handoffs between agents.
4. **Progressive disclosure in generated plugins.** Skill bodies stay <5k tokens; per-customer understanding lives in `config/data-understanding.json` (L3 reference) — skills cite it, don't inline it.
5. **Compiler stays authoritative.** The agent may propose KPI *ideas*; only `compiler/*` + `sqlglot` + dry-run produce shippable `kpi_defs.json`. Same trust boundary, broader input.

---

## 6. Target Artifact — `DataUnderstanding`

```python
# forge_core/models/data_understanding.py  (new, Pydantic v2, extra="forbid")

class ColumnUnderstanding(BaseModel):
    physical: ColumnProfile              # from structural.py (ground truth)
    map_entry: ColumnMapEntry            # percentiles, fingerprint, top_values, ambiguous
    role: Measure | Dimension | Identifier | Timestamp | Status | Text | Unknown
    business_name: str                   # "gross_amount" -> "Amount charged pre-discount"
    description: str                     # grounded in observed values, not invented
    unit: str | None                     # INR | USD | % | days | count ...
    vocabulary: list[ValueCount] | None  # for low-card enums, WITH frequencies
    sensitivity: Literal["none","pii","phi","financial"]
    evidence: list[Evidence]             # each claim cites tool output or stat
    confidence: float                    # 0..1, gated per claim type
    open_question: str | None            # if unresolvable

class TableUnderstanding(BaseModel):
    grain: TableGrain                    # extended: composite-key aware
    relationships: list[RelationshipFact] # with cardinality + orphan_rate + verified
    quality_issues: list[QualityIssue]   # with "impact: which KPIs / queries affected"
    temporal: TemporalProfile | None     # span, granularity, gaps, tz
    natural_description: str             # 1-2 sentences: what this table *is*

class DataUnderstanding(BaseModel):
    source_fingerprint: str              # hash for cache invalidation / drift
    tables: dict[str, TableUnderstanding]
    columns: dict[str, ColumnUnderstanding]  # key "table.column"
    domain: DomainAssessment             # pack match, matched/unmatched roles, evidence
    business_questions: list[BusinessQuestion] # ranked, with sql_sketch + support
    open_questions: list[OpenQuestion]  # must be resolved before generation or explicitly acknowledged
    glossary: dict[str, str]             # quick-lookup: "table.column -> plain English"
    provenance: Provenance               # model, token/step counts, run id
```

*Extends* `DataMap` — does not replace it. DataMap remains the **precomputed evidence store**; DataUnderstanding adds interpretation, hypotheses, and narrative. Written to `config/data-understanding.json` in every generated plugin.

---

## 7. Agent Architecture

### 7.1 Two-layer loop (same pattern as binding, generalized)

```
Layer 0  DETERMINISTIC PROFILERS  (no LLM, always runs, cheap, cached by fingerprint)
         structural.py + data_map.py + grain.py + relationships.py + quality.py
         → SchemaProfile + DataMap (stats, fingerprints, key-uniqueness, containment checks)

Layer 1  UNDERSTANDING SESSION(S) (LLM, bounded steps, tool-using)
         receives DataMap + unresolved-question queue; investigates ONLY ambiguous
         columns / unverified relationships / low-confidence glossary entries
         emits DataUnderstanding draft: roles, glossary, grain verdicts, domain,
         business questions, open questions — every claim with evidence

Layer 2  VERIFICATION GATES  (mechanical, no LLM judgment)
         claim↔evidence consistency, distribution sanity, key-uniqueness math,
         vocabulary coverage (test_value_set), PII cross-check, plausibility
         failures → fed back to Layer 1 once → else recorded as open_question
```

### 7.2 Specialist decomposition (one bounded session each, communicating via artifact)

| Specialist | Question it answers | Key tools | Output field |
|------------|---------------------|-----------|--------------|
| `grain-analyst` | One row = one *what*? | `uniqueness_probe`, `inspect_column` | `TableUnderstanding.grain` |
| `relationship-mapper` | How do tables join? fan-out? orphans? | `check_relationship`, `aggregate --group_by` | `TableUnderstanding.relationships` |
| `semantic-typist` | What does each ambiguous column *mean*? unit? | `inspect_column`, `compare_columns`, `test_value_set` | `ColumnUnderstanding.role/unit/vocabulary/description` |
| `domain-classifier` | Which pack fits? what has no counterpart? | (reads aggregated signals) | `DataUnderstanding.domain` |
| `question-hypothesizer` | What can this data credibly answer? | (reads DUM draft) + compiler dry-run | `DataUnderstanding.business_questions` (only survivors of sqlglot+dry-run ship) |
| `quality-auditor` | What would silently corrupt KPIs? | `aggregate`, `value_counts` | `TableUnderstanding.quality_issues` with KPI impact |
| `pii-scanner` | Sensitivity per column? | regex patterns + LLM semantic pass | `ColumnUnderstanding.sensitivity` |

Why specialists: narrower prompts, smaller contexts, cassette-testable in isolation, easy to run deterministically when `--no-llm`.

### 7.3 Investigation toolbox (superset of `agentic/investigation_tools.py:354-436`)

Existing six tools are correct — keep the **"params, never SQL text"** invariant (`investigation_tools.py:1-15`). Additions for the understanding scope:

| New tool | What it proves |
|----------|----------------|
| `uniqueness_probe(table, columns[])` | Composite-key grain testing (the missing piece in `grain.py:11-40`) |
| `value_counts(table, column, limit)` | Full vocabulary with frequencies (for enum ↔ free-text disambiguation) |
| `temporal_profile(table, column)` | Span, granularity, gaps, tz consistency (for date columns) |
| `cross_tab(table, col_a, col_b)` | Dependency discovery (e.g. `status` ⊥ `amount`?) |
| `search_values(literal)` | Where does a string appear across the source? (cross-table entity tracing) |
| `join_probe` | Alias of `check_relationship` with orphan-rate foregrounding |

All share one `_Toolkit` allowlist (`investigation_tools.py:43-85`), one `evidence_sink` (`:359-381`), same deny-list and row limits.

---

## 8. How Understanding Becomes *Customized* Plugin Components

This is the payoff — the mapping that makes skills/tools feel non-generic:

| DUM element | Feeds | Customization example (what user sees) |
|-------------|-------|----------------------------------------|
| `business_questions` (ranked, validated) | `generation/skills.py` → SKILL.md; `generation/recipes.py` → slash commands | Skill ships "Revenue by month (verified)" and "/edtech:monthly-enrollment-trend" *with caveats specific to this data*, not a generic "explore your data" template |
| `grain` per table | `compiler/kpi_compiler.py` (COUNT vs COUNT DISTINCT), skill prose | "bookings: one row per booking_id (unique, 0% nulls) — safe to COUNT(*)" written into skill guardrails |
| `ColumnUnderstanding.vocabulary` (status enums + frequencies) | `generation/hooks.py` → `hooks/hooks.json` (SessionStart + PreToolUse rules), skill guardrails section | Hook reminds model: "status has ['confirmed','cancelled','no_show'] — 12% cancelled — revenue queries must filter cancelled; P2-02's flag is exactly the surface to code this" |
| `glossary` + per-column descriptions | Skill body (L2), agent system prompt | "bookings.status = enrollment outcome; 'no_show' means booked but not attended — exclude from attendance KPIs" |
| `domain.matched / unmatched_roles` | Wizard review screen; skill "Not available for this data" section | "No refund column found — refund-rate KPI omitted (honest gap, not silent zero)" |
| `temporal` profiles | Command defaults, dashboard date defaults | `/monthly-report` auto-binds the real event-time column; warns if user passes load-time column |
| `sensitivity` map | `packaging/redaction.py` + PII gate | PII columns excluded from `artifacts/dashboard.html` samples and skill examples automatically |
| `quality_issues` with impact | Dashboard annotations + skill "Data caveats" section | "fee_amount has 3% negative values — likely refunds not modeled as separate rows — treat with care" |

**Authoring rule** for skill builders (from progressive-disclosure spec): keep `SKILL.md` under ~5k tokens, state conclusions in prose, point to `config/data-understanding.json` for depth. Claude loads L1 metadata cheaply, L2 body on activation, L3 understanding file only when the question needs it.

---

## 9. Phased Build Plan

Each phase is shippable and independently verifiable. No phase trusts an LLM output without a mechanical check.

### Phase U1 — Formalize the artifact (1 week)

- Create `forge_core/models/data_understanding.py` (schema §6).
- Extract understanding concern from `agentic/data_understanding_agent.py` (currently binding-scoped) into `forge_core/understanding/` package; binding agent becomes a *consumer* of DUM.
- Emit `config/data-understanding.json` on every run; add to validation harness (JSON-schema + provenance-present) and to `packaging/plugin_builder.py:write_plugin` allowlist.
- **Done when**: `forge run fixtures/datasets/bookings.csv --out /tmp/x` writes `config/data-understanding.json` even with `--no-llm` (deterministic subset populated, LLM fields null with provenance).

### Phase U2 — Harden deterministic layer (highest ROI, zero LLM cost) (1–2 weeks)

Upgrades to `profiling/*` (no LLM, no new dependencies):

- Composite-key uniqueness search + near-unique (99%+ distinct) reporting (`profiling/grain.py`).
- Full `value_counts` + enum-vs-free-text classifier beyond `structural.py:52-76` cardinality heuristic (use `avg_length` + `top_values` spread from `data_map.py`).
- Format fingerprints: phone, Aadhaar/PAN, ISO-datetime-vs-epoch, currency symbols, boolean-as-string.
- Temporal profiling: span, granularity detection, gap scan.
- Unit inference: currency column paired with country/region column.
- Containment-based join candidate scoring beyond name similarity.

**Done when**: ≥80% of columns on `fixtures/datasets/*.csv` + a 2-table SQLite fixture are fully understood with zero LLM calls; agent only sees residual ambiguous queue (today: ~40% ambiguous on typical MIS CSV — target: <20%).

### Phase U3 — Agentic understanding sessions (2 weeks)

- Implement orchestrator + specialists (§7.2) on existing `agentic/` plumbing (`AgentCallRecorder`, `build_investigation_tools`, `MAX_AGENT_STEPS`).
- Hypothesis-driven protocol: claim → evidence needed → minimal tools → update DUM.
- Abstention contract: unresolvable ⇒ `open_question`, never confident wrong claim.
- `--use-agent` flag generalized from binding/semantic to understanding.

**Done when**: cassettes replay deterministically; token/step budgets enforced; `open_questions` surface in API `NEEDS_INPUT` pause alongside `classification` pause.

### Phase U4 — Business-question synthesis (1 week)

- `question-hypothesizer` generates 8–15 candidates, each with `sql_sketch`.
- Sketches validated through existing `compiler/*` → `sqlglot` → `validation/dry_run.py` — only survivors become skills/recipes/commands.
- Rank by `support` (fraction of required columns present + row coverage).

**Done when**: generated skill lists 5+ customer-specific answerable questions that actually execute; unanswerable questions appear as "Not available — missing: X" with honest reason.

### Phase U5 — Wire understanding into generators (1–2 weeks)

- `generation/skills.py:generate_skill` — inject grain explanations, status vocabularies + mandatory-filter notes, glossary excerpts, temporal coverage.
- `generation/agents.py:generate_agent` — per-data system prompt (not just KPI ids + tool list).
- `generation/hooks.py:generate_hooks` — data-aware SessionStart reminder + optional PreToolUse predicates (e.g., "run_safe_query on bookings without WHERE status != 'cancelled' → warn").
- Wizard UI: render `open_questions` + `unmatched_roles` for review before packaging.

**Done when**: two plugins generated from different fixtures are byte-different in ways a non-technical reviewer can see are *about the data*, not the pack — snapshot tests on `generated/*/skills/*/SKILL.md` assert presence of per-data sections (grain, vocabularies, caveats).

### Phase U6 — Measure understanding (ongoing, starts at U1)

- Golden fixtures: N datasets with hand-labeled truth (grain, relationships, role, domain).
- Metrics: role-binding F1, grain accuracy, relationship precision/recall, PII recall, **question-support validity**, **skill-specificity rubric** (LLM-judge: does SKILL.md cite customer-specific facts? generic boilerplate = fail).
- Cost caps: token budget regression per phase; cache DUM by `source_fingerprint`.

---

## 10. Evaluation — How to Know It Works

| Signal | How measured | Target |
|--------|-------------|--------|
| Understanding is correct | Golden fixtures (hand-labeled) vs DUM: role F1, grain accuracy, relationship P/R, domain accuracy | ≥0.85 F1 on fixtures before wiring to generation |
| Understanding is *useful* | Downstream: binding F1, KPI compile success rate, dry-run pass rate vs pre-DUM baseline | Strict improvement; no regression on `tests/e2e/test_genericity.py` |
| Skills are *customized* (not generic) | LLM-judge rubric on generated `SKILL.md`: cites specific table/column/value/frequency; lists data-specific caveats | ≥80% of skills score "specific" on 20-question eval set |
| Skills are *safe* | Existing 8-check harness + new `data-understanding` schema check + `claude plugin validate --strict` | Zero hard-fail shipped |
| Cost is bounded | `AgentCallRecorder.summary()` per run; p50 token count vs char-budget | Deterministic layer handles ≥80% of columns without LLM |

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Plausible-wrong semantics poisoning KPIs | Gates (§7.1 L2) + confidence thresholds + `open_question` → human review before generation |
| Token blowup on wide schemas (100+ columns) | Deterministic triage first; `render_prompt(char_budget=30_000)` summarization; specialists batch columns |
| LLM cost per run | Cache by `source_fingerprint`; re-run understanding only on drift; Layer 0 is free |
| Overfitting to CSV-scale, breaking on warehouses | Keep DUM storage-engine-neutral; ingestion adapters (`ingestion/registry.py`) already abstract this |
| Silent drift when customer data changes | Fingerprint mismatch ⇒ stale-DUM warning surfaced by generated hook |
| Prompt injection via data values | Values are treated as data, never instructions; tool params are allowlisted; no raw value interpolation into SKILL.md prose without sanitization |

---

## 12. What Ox Alpha's Plan Covers vs This One

| Ox Alpha (`data-understanding-agent-plan.md`) | This plan |
|-----------------------------------------------|-----------|
| Defines DUM schema, two-layer loop, 6 specialists, mapping table, U1–U6 roadmap — strong on **semantic-layer-as-accuracy** thesis and DUM→component wiring | Same destination, different path: starts from **audited code** (line-level refs), adds **progressive-disclosure for generated skills**, **retrieval-based schema linking** for scale, explicit **skill-specificity eval**, and a concrete **artifact-first** migration out of `agentic/data_understanding_agent.py` |
| Use either as the spec; use both as the review checklist. |  |

---

## 13. References

- Architecture docs: `docs/architecture.md`, `docs/generator-flow.md`, `docs/source-architecture-doc.md`, `docs/plugin-format.md`, `docs/security.md`
- Code refs: `profiling/structural.py:16-126`, `profiling/semantic.py:25-141`, `profiling/data_map.py:34-212`, `models/data_map.py:18-64`, `models/schema_profile.py:22-154`, `agentic/data_agent.py:37-160`, `agentic/investigation_tools.py:37-447`, `generation/skills.py:59-104`, `generation/agents.py:30-73`
- Anthropic: *Equipping agents for the real world with Agent Skills* (engineering, Oct 16 2025) — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Agent Skills spec — https://agentskills.io/specification (progressive disclosure L1→L3)
- dbt Labs 2026 benchmark — *Semantic Layer vs Text-to-SQL* — https://www.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026 (98–100% SL vs 50–62% raw on modeled data)
- Production guide — *Text-to-SQL with LLMs in Production: Schema Linking, Query Validation, and Guardrails* (2026) — https://aiworkflowlab.dev/article/text-to-sql-llm-production-schema-linking-guardrails-2026
- Denodo baseline vs semantic layer (+67 pp BIRD, +33 pp Spider) — https://www.datamanagementblog.com/improving-the-accuracy-of-llm-based-text-to-sql-generation-with-a-semantic-layer-in-the-denodo-platform/
- Discovery pattern: Datachecks, LLMDap (VLDB 2025 workshop) — deterministic stats → LLM interpretation, hybrid PII detection
- Claude Code plugins reference — https://code.claude.com/docs/en/plugins-reference
