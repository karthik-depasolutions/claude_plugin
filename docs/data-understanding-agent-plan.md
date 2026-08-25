# Data-Understanding Agent — Build Plan

> Scope: the agent that gives Data2plugin a *complete, evidence-backed understanding* of any
> connected data source (CSV / Excel / JSON / Parquet / SQLite / Postgres), so every generated
> tool, hook, skill, agent, and KPI is **customized to that specific data — not generic**.
>
> Status of surrounding system: plugin generation pipeline exists (ingest → profile → classify →
> bind → compile → generate → validate → package). This plan covers ONLY the "understand" core,
> and how its output feeds the rest.

---

## 1. Research findings that shape this design

| # | Finding | Source | Design consequence |
|---|---------|--------|-------------------|
| 1 | Semantic-layer-style approaches hit **98–100% accuracy vs 84–90% for raw text-to-SQL** with current models (claude-sonnet-4-6 / gpt-5.3-codex benchmark); text-to-SQL fails *silently* (plausible-but-wrong numbers), semantic layers fail *loudly* | [dbt Labs 2026 benchmark](https://www.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026) | The agent's job is NOT to answer questions ad hoc. It is to **produce the semantic substrate** (roles, grains, relationships, vocabularies) that the existing KPI compiler turns into verified SQL. Keep the compiler authoritative. |
| 2 | Best-practice discovery agents are **deterministic-first**: compute stats/format fingerprints in code, use LLM only for *semantic interpretation* of those facts (>90% claimed column-meaning accuracy when grounded in real values) | [Datachecks Discovery](https://www.datachecks.io/datachecks-discovery-agent), [LLMDap (VLDB 2025)](https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/DEC/DEC25_5.pdf) | Never ask the LLM "what does this table contain?" cold. Always attach computed distributions, top values, min/max, formats. This is already the DataMap pattern — extend it, don't replace it. |
| 3 | Skills must use **progressive disclosure**: Level 1 metadata (~100 tokens, always loaded) → Level 2 body (<5k tokens, loaded on activation) → Level 3 references (loaded on demand) | [Anthropic Agent Skills spec](https://agentskills.io/specification), [Claude Platform docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) | Generated skills embed *pointers* into the understanding artifact, not dumps of it. One generic skill body + per-customer `data-profile.json` reference = non-generic behavior at near-zero context cost. |
| 4 | Multi-agent decomposition (schema understanding → query gen → validation as separate specialists) measurably improves accuracy and explainability vs single-prompt pipelines | [Text-to-SQL survey (arXiv 2410.01066)](https://arxiv.org/html/2410.01066v2), production guides | Split understanding into specialist subagents with narrow outputs, not one mega-prompt. |
| 5 | PII detection is best as **hybrid regex + LLM semantic** — either alone misses fields | [Labelbox](https://labelbox.com/blog/how-to-use-llms-to-detect-and-extract-personal-data-from-ai-datasets/) | Extend existing `validation/pii.py` into the understanding pass itself (sensitivity is part of understanding, not just a packaging gate). |
| 6 | Schema linking as retrieval (select relevant tables/columns before reasoning) beats stuffing full schemas into prompts | [AI Workflow Lab production guide](https://aiworkflowlab.dev/article/text-to-sql-llm-production-schema-linking-guardrails-2026) | For wide sources (>50 tables/columns), the agent must triage: profile everything cheaply, reason deeply only about candidates. |
| 7 | Claims must be verified by gates, never trusted blindly; failure should be *abstention*, not guesswork | your own P2-05/P2-06 design (correct); echoed by all above | Every understanding claim carries `{method, evidence, confidence}` and passes gates before consumers use it. |

---

## 2. Definition of "understands the data"

The agent is done when it can emit a single versioned artifact — the **Data Understanding Model
(DUM)** — answering all of these with evidence:

1. **Structure** — what tables/files/sheets exist, row counts, column types, keys.
2. **Shape** — the *grain* of each table (one row = one what?), which prevents double-counting.
3. **Relationships** — how tables join (explicit FKs + inferred), join-cardinality, orphan rates.
4. **Semantics** — what each column *means in business terms*: measures vs dimensions vs
   identifiers vs timestamps vs statuses; units (₹/$/%/days); enum vocabularies with frequencies.
5. **Domain** — what business this is (edtech booking ledger? clinic appointments?) with matched
   industry pack + evidence, and *which pack roles have no counterpart* in this data.
6. **Time** — temporal span, event-time vs load-time columns, seasonality hints, timezone traps.
7. **Quality** — null/duplicate/outlier/referral-integrity problems that would silently corrupt KPIs.
8. **Sensitivity** — PII/regulation exposure per column.
9. **Business questions** — the 5–15 questions this dataset *can* credibly answer, ranked by
   support in the data. ← this is what makes downstream analytics "customized, not generic".
10. **Open questions** — what could not be resolved automatically, for the human review step.

Formalize as `models/data_understanding.py`:

```python
class ColumnUnderstanding(BaseModel):
    physical: ColumnProfile            # from existing profiling/structural.py
    role: Measure | Dimension | Identifier | Timestamp | Status | Text | Unknown
    business_name: str                 # e.g. "gross_amount" -> "Amount charged pre-discount"
    description: str                   # grounded in observed values, never invented
    unit: str | None                   # currency=INR, pct, days...
    vocabulary: list[ValueCount] | None  # for low-cardinality cols, WITH frequencies
    sensitivity: Sensitivity           # none | pii | phi | financial
    evidence: list[Evidence]           # every claim cites tool output or stat
    confidence: float                  # 0..1, gated thresholds per claim type

class TableUnderstanding(BaseModel):
    grain: GrainHypothesis             # candidate + supporting key-uniqueness stats
    relationships: list[Relationship]  # incl. inferred ones + cardinality + orphan rate
    quality_issues: list[QualityIssue]
    temporal: TemporalProfile | None

class DataUnderstandingModel(BaseModel):
    source_fingerprint: str            # hash for cache-invalidation & drift detection
    tables: dict[str, TableUnderstanding]
    domain: DomainAssessment           # pack match, matched/unmatched roles, evidence
    business_questions: list[BusinessQuestion]  # question, sql_sketch, tables_used, support
    open_questions: list[OpenQuestion]
    provenance: Provenance             # model, token/step counts (AgentCallRecorder), run id
```

This extends — does not replace — the existing `DataMap` (`models/data_map.py`). DataMap stays the
*precomputed evidence store*; DUM adds interpretation, hypotheses, and narrative.

---

## 3. Agent architecture

### 3.1 Two-layer loop (deterministic floor, agentic ceiling)

```
Layer 0  DETERMINISTIC PROFILERS (no LLM, always runs, cheap, cached)
         structural.py · relationships.py · grain.py · quality.py · semantic.py
         → produces DataMap v2: stats, fingerprints, top-values, key-uniqueness,
           containment checks (A.col ⊆ B.col ⇒ join candidate), null matrices

Layer 1  UNDERSTANDING AGENT SESSION (LLM, bounded steps, tool-using)
         receives DataMap + unresolved-question queue
         investigates ONLY what Layer 0 flagged ambiguous or interesting
         emits DUM draft: roles, glossary, grain verdicts, domain match,
         business questions, open questions — every claim with evidence

Layer 2  VERIFICATION GATES (no LLM judgment; mechanical checks)
         claim-vs-evidence consistency, distribution sanity, uniqueness math,
         plausibility (existing validation/plausibility.py), PII cross-check
         failures → fed back to Layer 1 once → else recorded as open_question
```

This mirrors your proven binding-tier philosophy (P2-05/P2-06) but generalized from
"bind roles to columns" to "understand the whole source".

### 3.2 Specialist subagents (spawned by the orchestrator, one bounded session each)

| Subagent | Input | Output | Notes |
|---|---|---|---|
| `grain-analyst` | key stats, duplicates | grain verdicts | reuse `profiling/grain.py`; agent resolves ties |
| `relationship-mapper` | containment/join probes | join graph + cardinalities | extends `profiling/relationships.py` |
| `semantic-typist` | DataMap slices | per-column roles, units, vocabularies | the heart; batch columns per call |
| `domain-classifier` | aggregated signals | pack match + unmatched roles | feeds `classification/`, adds evidence |
| `question-hypothesizer` | DUM draft | ranked business questions + SQL sketches | sketches go through kpi_compiler paths, never shipped raw |
| `quality-auditor` | quality stats | prioritized issue list w/ KPI impact | "cancelled rows are 12% — any revenue SUM must filter" |
| `pii-scanner` | samples+patterns | sensitivity map | hybrid regex+LLM per research finding #5 |

Subagents communicate **only through the DUM artifact**, never through free-text handoffs —
keeps sessions small, replayable, and testable with cassettes (you already have
`llm/cassette.py`).

### 3.3 Investigation toolbox (superset of current `investigation_tools.py`)

```
inspect_column(table, col)          # full distro, percentiles, format fingerprint
compare_columns(a, b)               # correlation / containment / same-value check
sample_rows(where, order_by)        # targeted sampling, not blind head()
value_counts(col, limit)            # categorical vocabularies
cross_tab(a, b)                     # dependency discovery (status ⊥ amount?)
temporal_profile(ts_col)            # span, gaps, granularity, tz consistency
join_probe(left, right, on)         # match rate, cardinality, orphan count
uniqueness_probe(cols[])            # composite-key testing for grain
profile_regex(col, pattern)         # format hypothesis testing (IDs, phones)
search_values(literal)              # where does this string appear across source?
finish(summary)                     # terminal tool, exactly once
```

Guardrails identical to today's: read-only, LIMIT-enforced, step budget (~15–25),
token accounting via `AgentCallRecorder`.

---

## 4. How understanding becomes *customized* plugin components

Every generated component gets its specificity from the DUM — this is the payoff mapping:

| DUM element | Feeds | Customization example |
|---|---|---|
| business_questions | `generation/recipes.py` → skills | Skill ships "Revenue by month (verified)" instead of generic "explore your data" |
| grain verdicts | `compiler/kpi_compiler.py` | COUNT vs COUNT(DISTINCT) chosen correctly per metric |
| status vocabularies + quality issues | hooks (`generation/hooks.py`) | PreToolUse hook blocks KPI SQL lacking the mandatory `WHERE status != 'cancelled'` filter |
| glossary + descriptions | skill bodies (Level 2) | "bookings = confirmed+completed sessions; trials excluded" written into the skill prose |
| unmatched pack roles | wizard UI review step | "No refund column found — refund-rate KPI will be omitted" (honest gap, not silent) |
| temporal profiles | commands/defaults | `/monthly-report` auto-binds the real event-time column, warns on load-time misuse |
| sensitivity map | `packaging/redaction.py` + pii gate | PII columns excluded from sample artifacts automatically |
| domain assessment | skill template selection | edtech pack → attendance/fee-defaulter skills; clinic pack → no-show skills |

Skill authoring rule (from finding #3): generated skills stay under ~5k tokens, carry the DUM
*conclusions* in prose, and point to `config/data-understanding.json` (Level 3 reference) for
depth — so Claude Desktop loads cheaply and answers specifically.

---

## 5. Phased roadmap

### Phase U1 — Consolidate & formalize (foundation)
- Extract the understanding concern out of `agentic/data_understanding_agent.py` (currently
  binding-only) into `forge_core/understanding/` package; binding agent becomes a *consumer* of DUM.
- Define `models/data_understanding.py` (schema above); make DataMap → DUM conversion explicit.
- Emit `config/data-understanding.json` into every generated plugin; add to validation harness
  (schema-check + provenance-present).

### Phase U2 — Deepen deterministic layer (highest ROI, zero LLM cost)
- Upgrades to `profiling/`: composite-key uniqueness search, containment-based join inference,
  format-fingerprint library (currency symbols, phone/ID patterns, ISO dates vs epoch),
  unit detection, enum-vs-free-text classifier, temporal gap analysis.
- Target: ≥80% of columns fully understood with zero LLM calls on typical MIS datasets;
  agent effort concentrated on genuinely ambiguous residue.

### Phase U3 — Agentic understanding sessions
- Implement orchestrator + specialist subagents (§3.2) on the existing session/runtime plumbing.
- Hypothesis-driven protocol: agent states a claim → names the evidence needed → runs minimal
  tools → updates DUM. No fishing expeditions.
- Abstention contract: unresolvable ⇒ `open_question`, never a confident wrong claim.

### Phase U4 — Business-question synthesis
- Question-hypothesizer generates ranked candidate questions with SQL sketches.
- Sketches validated through the same compile→dry-run→plausibility path as pack KPIs
  (`validation/dry_run.py`) — only survivors ship into skills/commands.

### Phase U5 — Feedback into generation
- Wire DUM into generators: skills prose, hook predicates (mandatory-filter enforcement),
  command defaults, wizard review screen rendering open questions + unmatched roles.

### Phase U6 — Evals & CI (make "understanding" measurable)
- Golden fixtures: N datasets with hand-labeled truth (grain, relationships, roles, domain).
- Metrics: role-binding F1, grain accuracy, relationship precision/recall, PII recall,
  question-support validity, **skill-specificity rubric** (LLM-judge: does generated skill cite
  customer-specific facts? generic boilerplate = fail).
- Cassette-replay tests for agent determinism; token-budget regression caps per phase.

---

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Plausible-wrong semantics poisoning every KPI | Gates (§3.1 Layer 2) + confidence floors + human review of open_questions before generation proceeds |
| Token blowup on wide/large schemas | Deterministic triage first; agent sees only ambiguity queues; progressive disclosure in shipped skills |
| LLM cost per run | Cache DUM keyed by `source_fingerprint`; re-profile only on drift; Layer-0 handles most sources agent-free |
| Overfitting to CSV-scale, breaking on warehouses | Ingestion adapters already abstract this; keep DUM schema storage-engine-neutral |
| Silent drift when customer data changes | Fingerprint mismatch ⇒ stale-DUM warning surfaced by a generated hook |

---

## 7. References

- dbt Labs — *Semantic Layer vs Text-to-SQL: 2026 Benchmark* — https://www.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026
- Anthropic — *Equipping agents for the real world with Agent Skills* — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Agent Skills specification — https://agentskills.io/specification · Claude plugins reference — https://code.claude.com/docs/en/plugins-reference
- *From Natural Language to SQL: Review of LLM-based Text-to-SQL Systems* — https://arxiv.org/html/2410.01066v2
- LLMDap: LLM-based Data Profiling and Sharing (VLDB 2025 workshop) — https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/DEC/DEC25_5.pdf
- Datachecks Discovery Agent (semantic inference + hybrid PII pattern) — https://www.datachecks.io/datachecks-discovery-agent
- Text-to-SQL production guide: schema linking, validation, guardrails (2026) — https://aiworkflowlab.dev/article/text-to-sql-llm-production-schema-linking-guardrails-2026
