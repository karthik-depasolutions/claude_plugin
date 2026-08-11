# POC — Mini Version of the Real System (Gemini Profiling + Gemini Generation)

**Important shift from the earlier draft:** the first version of this POC asked Cursor to hand-code a fixed plugin. That's useful for learning plugin mechanics, but it doesn't prove out the actual product — the actual product is *Gemini reading data and improvising the plugin*. This version fixes that: the POC now builds a **small working pipeline** that mirrors every stage of the real architecture, just scoped down to one dataset, one industry, no UI, no marketplace hosting. If this works, the real system is the same pipeline made robust, multi-industry, and productized — not a different thing.

---

## 1. What This POC Actually Builds

Not a plugin file by itself — a **script/mini-service** that *produces* the plugin, by actually running the pipeline stages:

```
[1] INGEST csv  →  [2] PROFILE (code + Gemini)  →  [3] CLASSIFY (fixed: healthcare, for POC)
    →  [4] GENERATE (Gemini improvises SKILL.md, MCP tool defs, recipe, artifact)
    →  [5] VALIDATE (fact-check + dry-run against the real CSV)
    →  [6] PACKAGE (writes the actual plugin folder to disk)
```

Every stage from the full architecture is represented, just simplified:
- One dataset (the sample CSV), not a connector library
- One hardcoded Industry Pack (Healthcare/MIS — written by you, not retrieved from a library), not a matching engine
- Local file output, not marketplace publishing

This is the correct scope for a POC: **prove the AI-driven pipeline actually works end to end**, before investing in making each stage production-grade.

---

## 2. Sample Data

Use the attached `sample_bookings_mis.csv` (20 rows of diagnostic lab booking data — same shape as Curelo's MIS data). Columns: `booking_id, customer_id, customer_name, phone, city, package_name, package_category, lab_partner, booking_date, report_delivery_date, amount_inr, payment_status, is_repeat_customer, household_id, age, gender, status`.

---

## 3. The Healthcare Industry Pack (write this once, by hand, feed it to Gemini as context)

Keep this small for the POC — a JSON or markdown file with:
- **Vocabulary**: booking = an order/transaction, customer, household (grouping), lab_partner (vendor), package (product/service)
- **KPI definitions** (formulas Gemini should recognize/use, not invent from scratch):
  - `total_revenue` = sum(amount_inr) where status = "Completed"
  - `repeat_customer_rate` = % of bookings where is_repeat_customer = "Yes"
  - `cancellation_rate` = % of bookings where status = "Cancelled"
  - `monthly_revenue_trend` = revenue grouped by month(booking_date)
  - `bookings_by_city` = count + revenue grouped by city
- **Guardrails**: never expose `phone` or `customer_name` in aggregate outputs; read-only queries only

This file is what makes Gemini's output "industry-specific" instead of generic — it's the seed of your real Industry Pack library.

---

## 4. The Prompt — Give This to Cursor Agent

```
You are building a proof-of-concept pipeline that reads a CSV data file, uses 
the Gemini API to understand and improvise around it, and generates a working 
Claude Code plugin from the output. This is a scoped-down version of a larger 
system — build it as a real, runnable pipeline, not a hardcoded template.

INPUT FILES ALREADY IN THIS PROJECT:
- sample_bookings_mis.csv (20 rows of healthcare diagnostic booking data)
- healthcare_industry_pack.json (KPI definitions, vocabulary, guardrails — 
  create this file yourself first, using the spec below, before writing the 
  pipeline code)

BUILD A PYTHON PIPELINE (call it generate_plugin.py + supporting modules) 
WITH THESE STAGES AS SEPARATE, INSPECTABLE FUNCTIONS:

STAGE 1 — INGEST
Load the CSV with pandas. Output: dataframe + basic file metadata.

STAGE 2 — PROFILE (code + Gemini, both required)
(a) Deterministic profiling in code: for every column compute dtype, null %, 
    cardinality, min/max, and a guessed semantic role (identifier, date, 
    currency, categorical, free text, boolean flag) using simple heuristics 
    (regex on column names, dtype checks). Output this as a JSON 
    "structural_profile".
(b) Gemini call: send the structural_profile plus 5 sample rows (not the full 
    dataset) to Gemini. Prompt it to: propose semantic meaning for any 
    ambiguous columns, suggest 2-3 candidate insights or patterns worth 
    building into KPIs/recipes, and flag any data quality concerns it notices. 
    Require Gemini to return structured JSON (insight text + confidence + 
    which column/columns it's based on) — not free prose.
Combine both into a single "schema_profile.json" file and save it to disk so 
you can inspect it directly.

STAGE 3 — CLASSIFY (simplified for POC)
Just load healthcare_industry_pack.json directly — skip building a real 
matching engine for this POC. Print a note confirming which pack was used.

STAGE 4 — GENERATE (Gemini improvises, grounded by real inputs)
Make FOUR separate Gemini calls. Each call's prompt MUST include the full 
schema_profile.json and the industry pack, and MUST explicitly instruct 
Gemini: "Only reference column and table names that appear in the schema 
profile below. If you need a column that doesn't exist, say so instead of 
inventing one." Each call:
  (1) Skill writer -> generates SKILL.md content (markdown) for a Claude 
      skill that analyzes this booking data — trigger description, 
      instructions, example questions.
  (2) Tool spec generator -> generates a JSON list of MCP tools to build: 
      always include describe_schema and run_safe_query, plus a get_kpi tool 
      per KPI Gemini decides is relevant (from the industry pack's KPI list 
      AND any new KPI Gemini proposes based on its Stage 2 insights, as long 
      as it only uses real columns).
  (3) Recipe generator -> generates one markdown slash-command file 
      (commands/monthly-report.md or similar, Gemini can name it) that 
      orchestrates 2-4 of the tools above into a report.
  (4) Artifact generator -> generates one simple HTML dashboard file (a bar 
      chart + a small table, plain HTML/JS, e.g. using Chart.js from a CDN) 
      bound to whichever KPIs Gemini decided are relevant.
Save all four raw outputs to a /generated_raw/ folder before validating them, 
so you can see exactly what Gemini proposed before any correction happens.

STAGE 5 — VALIDATE (this is mandatory, not optional)
(a) Fact-check: parse every column/table reference out of the Stage 4 outputs 
    (tool specs, SQL-like references) and check each one against the real 
    columns in schema_profile.json. Any reference to a column that doesn't 
    exist -> flag it and print a clear warning listing exactly what's wrong 
    and where.
(b) Self-critique pass: make one more Gemini call, passing it its own Stage 4 
    outputs plus the schema_profile, asking it to review its own work and 
    flag anything unsupported, contradictory, or referencing data that 
    doesn't exist. Print this critique.
(c) Dry-run: implement the generated get_kpi logic for real (translate 
    Gemini's tool spec into actual pandas/SQLite computations against the CSV) 
    and run each one, printing the actual computed value. If a get_kpi 
    definition can't actually be computed against the real data as specified, 
    flag it clearly rather than silently failing.
Save a validation_report.json summarizing every check and its pass/fail 
result.

STAGE 6 — PACKAGE
If validation passes (or passes with only warnings, not hard failures), 
assemble the real Claude Code plugin folder structure:
curelo-bookings-poc/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── skills/booking-analyst/SKILL.md      <- from Stage 4(1)
├── commands/<name>.md                    <- from Stage 4(3)
├── .mcp.json
├── mcp_server/server.py                  <- implements the validated tool 
│                                             specs from Stage 4(2), using the 
│                                             actual working logic proven in 
│                                             Stage 5(c), not raw Gemini output
└── README.md                             <- includes the artifact HTML from 
                                              Stage 4(4), install steps, and a 
                                              short summary of what was 
                                              auto-generated vs. hand-written

ALSO PRINT A CLEAR PIPELINE LOG TO THE CONSOLE as it runs, showing what 
happened at each of the 6 stages, so it's obvious this is a real pipeline 
and not a black box.

Use the actual Gemini API (not a mock) — assume GEMINI_API_KEY is available 
as an environment variable. Keep everything single-machine, no deployment, 
no database — this is a POC to prove the pipeline logic works end to end.
```

---

## 5. What To Check When It's Done

- [ ] `schema_profile.json` exists and Stage 2's Gemini insights are genuinely useful (not just restating the obvious)
- [ ] `/generated_raw/` shows Gemini's *unvalidated* first-pass output — read it, see if it hallucinated anything
- [ ] `validation_report.json` actually catches something — if Gemini never makes a single mistake across several runs, that's a sign your test isn't rigorous enough; try re-running a few times or removing a column to see if fact-checking catches it
- [ ] The self-critique pass output is genuinely reviewing, not just rubber-stamping — read it directly
- [ ] The final packaged plugin's `get_kpi` values match what you'd get by manually checking the CSV (e.g., manually sum `amount_inr` for Completed rows and compare to `total_revenue`)
- [ ] Install the packaged plugin in Claude Code and ask it real questions — confirm it behaves correctly

---

## 6. Why This Scope Is Right for a POC

This POC is small in *breadth* (one dataset, one industry, no hosting) but complete in *depth* — it exercises every real decision from the architecture: code-plus-Gemini profiling, Gemini improvising within guardrails, fact-checking against real schema, a self-critique pass, and dry-run verification before packaging. If this pipeline produces a plugin that actually works when installed in Claude Code, you've proven the core idea end to end — the production build from here is about making each stage robust (more file types, a real industry-matching engine, a marketplace, telemetry), not about validating whether the idea works at all.
