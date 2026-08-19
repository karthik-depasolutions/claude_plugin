# Implementation Plan — MIS Plugin Forge

**Audience:** a coding agent working in this repository.
**Companion doc:** `mis-plugin-forge-architecture-review.md` — read it if you need the *why* behind any task. This file is the *what* and *how*.

---

## 0. Read this before touching anything

### 0.1 What this system actually is

Do not trust the framing in `docs/`. The verified reality:

- The generation pipeline is **one linear synchronous function**: `run_pipeline` in `packages/forge-core/src/forge_core/orchestrator.py`. It is not a graph. **Do not convert it to a graph as part of this plan.**
- The runtime (`packages/mcp-runtime/`) exposes a **fixed set of 7 tools, identical for every customer**. Nothing is generated. This is correct — **do not add generated tools.**
- Capabilities come from hand-authored `industry-packs/<slug>/kpis/*.json`. Customer data only selects a pack and binds columns to canonical roles. Phase 1 does **not** change this; Phase 2 begins to.
- Two opt-in LangChain ReAct agents exist (`agentic/binding_agent.py`, `agentic/data_agent.py`). **Do not add more agents.** One task below makes the existing binding agent fire more often, which is the intended lever.

### 0.2 Ground rules

1. **One task per commit.** Task IDs below map 1:1 to commits. Use the given commit message.
2. **Read before writing.** Several tasks touch files that were not in the review package (`compiler/kpi_compiler.py`, `binding/resolver.py`, `validation/harness.py`). Each such task starts with a `VERIFY FIRST` block. Do that verification and report what you found before changing behaviour. If reality contradicts the task description, **stop and say so** rather than forcing the change.
3. **Never weaken a guardrail to make a test pass.** If `sql_policy`, `allowlist`, `pii_policy`, or `limits` blocks something, the caller is wrong, not the guard.
4. **Two things will break when outputs change, and both are expected:**
   - `fixtures/golden/` — regenerate deliberately, review the diff by eye, commit it separately with a note on what moved and why.
   - `fixtures/cassettes/` — recorded LLM responses. **Any prompt-string change invalidates them.** Re-record with `FORGE_LLM_CASSETTE_MODE=record` and a live `GEMINI_API_KEY`. Tasks that touch prompts are flagged.
5. **Pre-existing failures.** `tests/forge-core/test_profiling_semantic.py` has 2 known failures (orphaned cassette) and 11 Postgres skips. Baseline is **139 passed / 2 failed / 11 skipped**. Do not attempt to fix those two as part of this work; just don't add to the count.
6. **Verify against real artifacts, not unit tests alone.** Every Phase 1 task has an end-to-end check that regenerates a plugin from `fixtures/datasets/` and inspects the output. Unit tests can pass while the shipped artifact is still wrong — that is precisely the failure mode this plan exists to fix.

### 0.3 Baseline commands

```bash
# Full suite
uv run pytest packages/forge-core/tests packages/mcp-runtime/tests tests/e2e -q

# Regenerate the two reference plugins (no LLM key needed; deterministic path)
uv run forge run fixtures/datasets/bookings.csv   --out /tmp/gen/hc     --no-review
uv run forge run fixtures/datasets/edtech.sqlite  --out /tmp/gen/edtech --no-review

# Multi-table case — currently collapses to one table, target of P2-01
uv run forge run fixtures/datasets/retail_orders  --out /tmp/gen/retail --no-review
```

### 0.4 Task order and dependencies

```
P1-01 ─┐
P1-02 ─┤  independent, parallelisable
P1-03 ─┤
P1-05 ─┤
P1-06 ─┤
P1-07 ─┘
P1-04 ──► P1-08 ──► P1-09        (gate needs plausibility; both need the harness slot)
                      │
P2-01 ────────────────┴──► P2-02 ──► P2-03
```

Do Phase 1 in full before starting Phase 2. **P1-09 (the eval harness) is the acceptance gate for the entire plan** — without it you cannot demonstrate that anything improved.

---

# PHASE 1 — Correctness and security

Nine tasks. None changes the architecture. All are patches to existing code.

---

## P1-01 — Eliminate `eval()` on assertion strings

**Priority:** P0 (security) · **Effort:** ~2h · **Depends on:** nothing

### VERIFY FIRST

```bash
sed -n '1,80p' packages/forge-core/src/forge_core/compiler/kpi_compiler.py
grep -rn "assertion" packages/forge-core/src/forge_core/compiler/
```

Question to answer: **does `compile_kpi` validate the `assertions` strings in any way?**

- If it does not (expected) → proceed, this is a live RCE path.
- If it already AST-validates them → severity drops; still do the work, since the runtime should not rely on generator-side validation for its own safety.

Report which you found before continuing.

### The problem

`packages/mcp-runtime/src/mis_mcp_runtime/engine/kpi_executor.py`:

```python
_SAFE_BUILTINS = {"abs": abs, "min": min, "max": max, "round": round, "len": len}
passed = bool(eval(expr, {"__builtins__": _SAFE_BUILTINS}, row))  # noqa: S307
```

`expr` originates in `config/kpi_defs.json`. On the `--agent` path those strings are LLM-authored: `compiler/kpi_proposer.py`'s prompt asks literally for `"assertions": ["python expression over result columns"]`. Restricted-builtins `eval` is not a sandbox — the subclass-traversal gadget reaches `os` with no builtins at all. This executes inside Claude Desktop on the customer's machine.

### Changes

**1. New file** `packages/mcp-runtime/src/mis_mcp_runtime/engine/assertions.py`

Whitelist-only AST evaluator. Permit exactly: `Expression, BoolOp, BinOp, UnaryOp, Compare, Name, Load, Constant, Call, And, Or, Not, USub, UAdd`, the arithmetic ops `Add Sub Mult Div Mod Pow`, the comparisons `Eq NotEq Lt LtE Gt GtE`. Reject everything else — notably `Attribute`, `Subscript`, `Lambda`, comprehensions, `Starred`.

Two public functions:

- `validate_assertion(expr: str) -> None` — raises `AssertionPolicyError` on anything disallowed. Cap length at 200 chars. `ast.parse(expr, mode="eval")`, walk, check node types. For `Call`, require `isinstance(node.func, ast.Name)` and `node.func.id in {"abs","min","max","round","len"}`.
- `evaluate_assertion(expr: str, row: dict) -> bool` — calls `validate_assertion` again (defence in depth), then recursively evaluates. `Name` resolves from `row`; unknown name raises rather than returning a truthy default.

The full reference implementation is in §15.1 of the review doc. Adapt to this package's conventions; do not import from `forge_core` (mcp-runtime ships standalone — see its `config.py` docstring).

**2. Edit** `engine/kpi_executor.py` — replace `_check_assertions`:

```python
from mis_mcp_runtime.engine.assertions import AssertionPolicyError, evaluate_assertion

def _check_assertions(assertions: list[str], row: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for expr in assertions:
        try:
            results.append({"assertion": expr, "passed": bool(evaluate_assertion(expr, row))})
        except AssertionPolicyError as exc:
            results.append({"assertion": expr, "passed": False,
                            "error": f"rejected by assertion policy: {exc}"})
        except Exception as exc:  # noqa: BLE001 - report, never crash the tool call
            results.append({"assertion": expr, "passed": False, "error": str(exc)})
    return results
```

Delete `_SAFE_BUILTINS`. Confirm no other `eval(` / `exec(` remains: `grep -rn "eval(\|exec(" packages/mcp-runtime/src/`.

**3. Edit** `packages/forge-core/src/forge_core/models/industry_pack.py` — reject bad assertions at the generator boundary so they never reach disk:

```python
@field_validator("assertions")
@classmethod
def _assertions_are_safe(cls, v: list[str]) -> list[str]:
    for expr in v:
        validate_assertion(expr)
    return v
```

Import from a small shared copy in `forge_core/validation/assertion_policy.py` — **duplicate the validator rather than importing across the package boundary**, matching the existing deliberate `forge-core` ⇄ `mcp-runtime` independence. Add a test asserting the two whitelists are identical so they cannot drift.

`propose_kpis` already wraps `CanonicalKpi.model_validate` in `except Exception: continue`, so a malicious candidate is silently dropped. No change needed there.

### Tests

`packages/mcp-runtime/tests/test_assertions.py`:

- Accepts: `"total >= 0"`, `"pct >= 0 and pct <= 100"`, `"abs(delta) < 10"`, `"round(rate, 2) == rate"`
- Rejects (each its own case): `"().__class__.__bases__[0].__subclasses__()"`, `"__import__('os').system('id')"`, `"open('/etc/passwd').read()"`, `"[x for x in ().__class__.__mro__]"`, `"(lambda: 1)()"`, `"row.__class__"`, `"x" * 300`
- `evaluate_assertion("total >= 0", {"total": 5}) is True`
- Unknown name raises rather than silently passing
- Every `industry-packs/*/kpis/*.json` assertion passes `validate_assertion` (parametrised over the real files — this guards against a future pack author writing something the policy rejects)

### Acceptance

- [ ] `grep -rn "eval(" packages/mcp-runtime/src/` returns nothing
- [ ] All existing pack KPIs still evaluate their assertions correctly (`/tmp/gen/hc` shows `assertions: [{passed: true}]` for `cancellation_rate`)
- [ ] Full suite at baseline

**Commit:** `security: replace eval() with whitelist AST evaluator for KPI assertions`

---

## P1-02 — Enforce denied columns in every SQL clause

**Priority:** P1 (security) · **Effort:** ~2h · **Depends on:** nothing

### The problem

`packages/mcp-runtime/src/mis_mcp_runtime/security/pii_policy.py::check_no_denied_columns` iterates `select.expressions` — projections only. `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY` are unchecked. So this passes:

```sql
SELECT COUNT(*) AS n FROM src_bookings WHERE "customer_name" LIKE 'A%'
```

Binary search over a denied column reconstructs its values. `GROUP BY "phone"` leaks the distribution directly.

For file sources `redaction.py` physically removes those columns, which masks the hole. **For live-database sources it does not** — `write_redacted_data_files` returns early when `original_paths` is empty, which is exactly the Postgres path. There, this check is the only defence.

### Change

Replace the function body — walk every `exp.Column` in the whole statement tree, not just projections. Keep the existing alias-collision check. Reference implementation in review §15.4.

Update the docstring: it currently claims `SELECT *` rejection makes projections "checkable by name", which was the reasoning behind the narrow scope. State that all clauses are now covered.

### Tests

Extend `packages/mcp-runtime/tests/test_runtime_tools.py`:

- `SELECT COUNT(*) AS n FROM src_bookings WHERE "customer_name" LIKE 'A%'` → `PiiPolicyError`
- `... GROUP BY "phone"` → `PiiPolicyError`
- `... ORDER BY "customer_name"` → `PiiPolicyError`
- `... HAVING COUNT("phone") > 1` → `PiiPolicyError`
- A CTE referencing a denied column in its inner `WHERE` → `PiiPolicyError`
- Regression: every existing legitimate query still passes

### Acceptance

- [ ] All five leak paths blocked
- [ ] No compiled pack KPI is broken by the stricter rule — check `/tmp/gen/hc` and `/tmp/gen/edtech` still validate. If a pack KPI legitimately filters on a denied column, that is a **pack authoring bug**; report it, do not relax the guard.

**Commit:** `security: enforce denied-column policy across all SQL clauses, not just projections`

---

## P1-03 — Single source of truth for denied columns

**Priority:** P0 (correctness + PII) · **Effort:** ~4h · **Depends on:** nothing

### The problem — reproduce it first

```bash
uv run forge run fixtures/datasets/edtech.sqlite --out /tmp/gen/edtech --no-review
python3 - <<'PY'
import json, sqlite3
ds = json.load(open('/tmp/gen/edtech/edtech-mis-plugin/config/data_source.json'))
print("ADVERTISED:")
for t in ds['tables']: print(" ", t['name'], t['columns'])
c = sqlite3.connect('/tmp/gen/edtech/edtech-mis-plugin/data/edtech.sqlite')
print("SHIPPED:")
for (n,) in c.execute("select name from sqlite_master where type='table'"):
    print(" ", n, [r[1] for r in c.execute(f'PRAGMA table_info("{n}")')])
PY
```

You will see `courses.course_name`, `courses.subject`, `students.full_name`, `students.email` advertised but absent from the shipped database.

**Cause:** denied columns are computed twice, differently.

- `packaging/redaction.py::denied_columns_by_table()` — all tables, drops `is_likely_pii` **or** `guessed_role in pack.guardrails.denied_role_categories`. Drives physical deletion.
- `bindings.denied_columns` — fact table only; `[]` for edtech. Drives config filtering.

`plugin_builder._config_files()` filters `column_profiles` by the second but builds `schema_summary.tables[].columns` and `data_source.json` `tables[].columns` from `source.tables` **unfiltered**.

**Two harms:** `describe_schema` hands Claude phantom columns (grounding failure), and PII column *names* ship in a plugin whose `pii_scan` passed.

### Changes

**1. New file** `packages/forge-core/src/forge_core/packaging/denial.py`

Move `denied_columns_by_table` here verbatim, rename to `compute_denied_columns(profile, pack) -> dict[str, set[str]]`. This becomes the only implementation.

**2.** `packaging/redaction.py` — import it, delete the local copy, keep the re-export if anything depends on the old name.

**3.** `orchestrator.py` — compute once at PACKAGE, thread through:

```python
denied_by_table = compute_denied_columns(profile, pack)
spec = build_plugin_spec(..., denied_by_table=denied_by_table)
write_plugin(spec, plugin_dir, ..., denied_by_table=denied_by_table)
```

**4.** `packaging/plugin_builder.py::_config_files()` — filter **both** column lists:

```python
"tables": [
    {
        "name": t.name,
        "physical_ref": t.physical_ref,
        "columns": [c.name for c in t.columns
                    if c.name not in denied_by_table.get(t.name, set())],
        # ...
    }
    for t in source.tables
],
```

Apply the same filter to `schema_summary_json["tables"][*]["columns"]` and keep the existing `column_profiles` filter, now sourced from `denied_by_table`.

**Watch for:** `search_records` builds its projection from `config.data_source.tables[*].columns`. Filtering that list is exactly right — it means denied columns can no longer be requested. Confirm `test_runtime_tools.py` still passes.

### Tests

- `packages/forge-core/tests/test_redaction.py` — advertised columns for every table equal actual shipped columns
- New `test_packaging.py` case: given a profile with a PII column on a *non-fact* table, that column appears in neither `data_source.json` nor `schema_summary.json`

### Acceptance

- [ ] The reproduction script above shows ADVERTISED == SHIPPED for all three edtech tables
- [ ] `students.email` and `students.full_name` appear nowhere under `/tmp/gen/edtech/`: `grep -rn "full_name\|email" /tmp/gen/edtech/*/config/` returns nothing
- [ ] Golden fixtures regenerated, diff reviewed

**Commit:** `fix: single denied-column computation across redaction, config, and advertised schema`

---

## P1-04 — Binding plausibility check

**Priority:** P0 (correctness) · **Effort:** ~1d · **Depends on:** nothing · **Blocks:** P1-08

### The problem

`/tmp/gen/edtech/*/config/schema_bindings.json` contains:

```json
{ "role": "revenue_amount", "physical": "score", "confidence": 0.45,
  "evidence": "core-token overlap 0.00 for role 'revenue_amount'; column role numeric matches expected type" }
```

A test score bound to the role for money, on the strength of "it's a number." It passes all 8 validation checks. It causes no visible harm only because no edtech pack KPI references `{{revenue_amount}}` — add one and every customer sees revenue reported as the sum of test scores.

### VERIFY FIRST

```bash
cat packages/forge-core/src/forge_core/validation/harness.py
ls packages/forge-core/src/forge_core/validation/
```

Learn how checks register, what `ValidationCheckResult` / `ValidationIssue` require, and how `on_check` reporting works. Follow that pattern exactly.

### Change

**New file** `packages/forge-core/src/forge_core/validation/plausibility.py`

A rule table keyed on canonical role, each rule a `Callable[[ColumnProfile], str | None]` returning a human-readable problem or `None`. Reference implementation in review §15.6. Minimum rule set:

| Role pattern | Rule |
|---|---|
| `revenue_amount`, `*_amount`, `price*`, `*_value` (currency unit) | FAIL if `0 <= min <= max <= 100` — that shape is a score/percentage, not money. FAIL if `min < 0`. |
| `score` | FAIL if `max > 1000` |
| `*_date`, `*_at`, `transaction_date` | FAIL if `cardinality < 2` — time-series metrics are meaningless on one date |
| any `percent`/`rate` unit | FAIL if `min < 0` or `max > 100` |
| any `*_ref`, `*_id` identifier role | WARN if `distinct_ratio < 0.01` on a fact table — that is not an identifier |

Rules must be **data-driven from `ColumnProfile`**, never from the column name. The name is what got us here.

Register in `harness.py` as check #9, `binding_plausibility`, positioned **before** `dry_run` (fail fast, cheaply). Severity `error` blocks packaging; the existing `report.overall == "fail"` handling in `orchestrator.py` then sets `RunStatus.FAILED`.

### Tests

`packages/forge-core/tests/test_validation_plausibility.py`:

- The real edtech binding (`score` → `revenue_amount`, min 0 max 100) → FAIL, message names both the column and the reason
- `amount_inr` → `revenue_amount` (min 299, max 15000) → PASS
- A date column with one distinct value → FAIL
- A percent column with max 150 → FAIL
- Unknown role with no rule → PASS (rules are opt-in, never fail-by-default)

### Acceptance

- [ ] `uv run forge run fixtures/datasets/edtech.sqlite --out /tmp/gen/edtech --no-review` now **fails validation** with a message naming `score`/`revenue_amount`
- [ ] `uv run forge run fixtures/datasets/bookings.csv --out /tmp/gen/hc --no-review` still passes all checks
- [ ] The failure message is actionable — a human reading it knows exactly which binding to fix

> **Expected:** edtech will now fail to generate. That is correct behaviour and it is fixed properly by P1-08, which converts the failure into a question instead of a dead end. Do not weaken this check to make edtech pass.

**Commit:** `validation: add binding plausibility check against real column distributions`

---

## P1-05 — Tenant-scope the binding decision cache

**Priority:** P1 (security) · **Effort:** ~3h · **Depends on:** nothing

### The problem

`packages/forge-core/src/forge_core/agentic/memory.py::recent_examples()` queries on `(pack_slug, role)` with no tenant scoping, and `binding_agent.py::_examples_block` injects the result into the prompt as:

> `"On a different customer's schema, this concept was resolved to a column named 'X' because: <reasoning>"`

Customer B's agent sees Customer A's real column names and free-text reasoning. `apps/api` has auth and per-tenant runs, so this is a live multi-tenant leak.

### VERIFY FIRST

```bash
grep -rn "tenant\|user_id\|owner" apps/api/src/forge_api/models_orm.py apps/api/src/forge_api/routers/runs.py
```

Find the real tenant identifier. If there genuinely is none (single-tenant deployment), say so — severity drops and the fix becomes "add the column now so it's ready," not an urgent patch.

### Changes

**1.** `memory.py` — add `tenant_id TEXT NOT NULL DEFAULT '_local'` to the `binding_decisions` table. Since the table is created with `CREATE TABLE IF NOT EXISTS`, add a lightweight migration: check `PRAGMA table_info`, `ALTER TABLE ADD COLUMN` if absent. Extend the index to `(tenant_id, pack_slug, role, schema_fingerprint)`.

**2.** Thread `tenant_id` through `get_exact_decision`, `recent_examples`, `record_decision` as a **required** parameter — no default, so every call site must be updated deliberately.

**3.** `recent_examples` filters `tenant_id = ?` by default. Add `allow_cross_tenant: bool = False`; when False (always, for now) never return another tenant's rows.

**4.** `binding_agent.py::_examples_block` — even for same-tenant examples, stop leaking verbatim identifiers into the prompt. Replace the column name with a structural description:

```python
f"- A previous schema resolved this concept to a column whose name contained "
f"{_name_tokens(ex.column)!r} and whose values looked like: {ex.value_shape}"
```

Add a `value_shape` column (e.g. `"numeric, 4-figure, no negatives"`). If that is too large a change, at minimum gate the verbatim form behind same-tenant.

**5.** Thread `tenant_id` from `RunRecord` → `run_pipeline` → `resolve_bindings` → `propose_binding_with_agent`. Add `tenant_id: str = "_local"` to `RunRecord` and set it from the authenticated principal in `apps/api`.

⚠️ **This changes prompt text** → re-record cassettes.

### Tests

`packages/forge-core/tests/test_agentic_memory.py`:

- Record for tenant A, `recent_examples` for tenant B returns `[]`
- `get_exact_decision` for tenant B on tenant A's fingerprint returns `None`
- Migration: open a DB created without the column, confirm it upgrades and existing rows land in `_local`

### Acceptance

- [ ] No query in `memory.py` omits `tenant_id`
- [ ] Cross-tenant read returns nothing
- [ ] Existing on-disk `generated/agent_memory/*.sqlite` upgrades without data loss

**Commit:** `security: scope binding decision cache and few-shot examples by tenant`

---

## P1-06 — Fix hook event/handler pairing

**Priority:** P1 · **Effort:** ~2h · **Depends on:** nothing

### The problem

Generated `hooks/hooks.json` uses:

```json
"SessionStart": [{ "matcher": "*", "hooks": [{ "type": "prompt", "prompt": "..." }] }]
```

Per the current `plugin-dev` hook-development skill in `anthropics/claude-code`, `type: "prompt"` handlers are supported on **Stop, SubagentStop, UserPromptSubmit, PreToolUse**. `SessionStart` is not among them. Your only deterministic guardrail-injection mechanism — carrying "never expose phone or customer name", read-only enforcement, and the data-quality findings — most likely never fires.

### VERIFY FIRST

Do not take my word for it. Check both:

```bash
# 1. Does the validator catch it?
uv run forge run fixtures/datasets/bookings.csv --out /tmp/gen/hc --no-review
claude plugin validate /tmp/gen/hc/healthcare-diagnostics-mis-plugin

# 2. Does it actually fire? Install and inspect.
claude --plugin-dir /tmp/gen/hc/healthcare-diagnostics-mis-plugin --debug
```

Look for the guardrail text in session context. Report what you observe. If it *does* fire, close this task as invalid.

### Change

`packages/forge-core/src/forge_core/generation/hooks.py` — emit a `command` handler whose stdout is injected as SessionStart context:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command",
                     "command": "python \"${CLAUDE_PLUGIN_ROOT}/hooks/session_context.py\"" } ] }
    ]
  }
}
```

Generate `hooks/session_context.py` alongside — a tiny script reading `config/schema_summary.json` and printing the guardrail block plus data-quality findings to stdout. Advantages over the inline prompt: it reads live config, so regeneration of `config/` alone keeps guardrails current, and the content is not duplicated across two files.

Keep it under ~200ms; hook latency is prepended to every session start.

### Tests

- `test_generation.py` — emitted `hooks.json` has no `prompt` handler on `SessionStart`
- New: the generated `session_context.py` runs against a fixture config dir, exits 0, and prints the denied-column guardrail
- Extend `mcp_smoke`-adjacent validation (or add a `hooks_smoke` check) to actually execute the script and assert exit 0 + non-empty stdout

### Acceptance

- [ ] Guardrail text is verifiably present in session context on a real install
- [ ] `claude plugin validate` passes
- [ ] Data-quality findings still reach the session

**Commit:** `fix: emit SessionStart hooks as command handlers so guardrails actually fire`

---

## P1-07 — Instrument agent LLM calls

**Priority:** P2 (observability) · **Effort:** ~3h · **Depends on:** nothing

### The problem

Per `07_trace_log/TRACE_LOG.md`: both agents construct `ChatGoogleGenerativeAI` directly rather than going through the instrumented `GeminiProvider`. Your cost telemetry therefore omits the most expensive component. In the recorded run this shows up as unexplained 15–17s gaps between stage timestamps.

You cannot optimise what you cannot see, and P2 work will change agent invocation frequency (P1-08 makes the binding agent fire more often). Get the baseline first.

### Changes

**1.** `packages/forge-core/src/forge_core/llm/provider.py` — add a callback-based token recorder, or use LangChain's `BaseCallbackHandler` capturing `on_llm_end` usage metadata.

**2.** `agentic/binding_agent.py` and `agentic/data_agent.py` — pass the handler into `create_agent`'s config:

```python
agent.invoke(
    {"messages": [...]},
    config={"recursion_limit": MAX_AGENT_STEPS, "callbacks": [token_recorder]},
)
```

**3.** Emit a `StageEvent` per agent invocation carrying `{steps, input_tokens, output_tokens, thinking_tokens, wall_seconds, tool_calls}`. `RunRecord.log()` already threads `**data`.

**4.** Extend `07_trace_log/produce_trace.py` to include agent rows in its table.

### Acceptance

- [ ] A `--agent` run produces per-agent token counts in `record.events`
- [ ] `produce_trace.py` output shows agent calls, not just the 7 single-shot ones
- [ ] Record and commit a new baseline trace — it is the comparison point for Phase 2

**Commit:** `observability: instrument agent LLM calls with token and step accounting`

---

## P1-08 — Binding confidence gate

**Priority:** P0 (the highest-value change in Phase 1) · **Effort:** ~1d · **Depends on:** P1-04

### The idea

P1-04 makes a bad binding *fail*. This makes it *ask*. Right now every binding ships regardless of confidence: the edtech example shipped six bindings between 0.45 and 0.86 with `unresolved_roles: []`, because everything bound to *something*.

There is a second effect worth understanding. `binding/resolver.py` runs three tiers — deterministic scorer → single-shot LLM → binding agent — and falls through only on *failure*. A 0.45 score counts as success, so the agent never runs. The agent has `preview_column_values` and would very likely have seen `[88, 72, 91, 45]` and rejected the revenue binding. **Raising the bar for "resolved" routes your existing agent at exactly the decisions it was built for.** No new agent required.

### VERIFY FIRST

```bash
cat packages/forge-core/src/forge_core/binding/resolver.py
cat packages/forge-core/src/forge_core/binding/scorer.py
```

Confirm: what counts as "resolved"? Is there any threshold today? Does the scorer expose runner-up candidates (needed for the UI)? Report before changing.

### Changes

**1.** `binding/resolver.py` — introduce `MIN_CONFIDENCE_RESOLVED = 0.70`. A tier's result below this does **not** terminate the fallthrough; continue to the next tier. If the final tier is still below threshold, keep the best candidate but mark it `needs_confirmation=True`.

**2.** `models/bindings.py` — add to `ColumnBinding`:

```python
needs_confirmation: bool = False
alternatives: list[tuple[str, float]] = Field(default_factory=list)
```

**3.** New file `binding/gate.py` — `gate_bindings(bindings, pack, kpi_defs_preview) -> list[BindingQuestion]`. Reference implementation in review §15.2. Two rules that matter:

- **Only gate roles a shipped KPI actually uses.** Compute `roles_in_use` from the compiled KPI set. Never make a user confirm a binding nothing depends on — a long gate is a gate people click through.
- **Human overrides are sticky.** `source == "human_override"` is never re-asked.

**4.** `orchestrator.py` — after `resolve_bindings` and a preview compile, before the real `compile_all`:

```python
binding_questions = gate_bindings(bindings, pack, kpi_defs_preview)
if binding_questions and record.binding_confirmations is None:
    record.status = RunStatus.NEEDS_INPUT
    record.binding_questions = binding_questions
    record.log(RunStage.BIND, "Awaiting binding confirmation",
               questions=[q.model_dump() for q in binding_questions])
    return
```

**5.** `models/run.py` — add `binding_questions: list[BindingQuestion] = []` and `binding_confirmations: dict[str, str] | None = None`. Mirror the existing `data_answers` three-state convention exactly: `None` = never asked, `{}` = asked and declined, populated = answered. That distinction is what stops the pause re-firing on resume — see the `data_answers` docstring.

**6.** Apply confirmations on resume: a confirmation sets `source="human_override"`, `confidence=1.0`. If the answer is "none of these," add the role to `unresolved_roles` so dependent KPIs land in `.skipped` with a clear reason — which is the correct outcome, not a failure.

**7.** `apps/api` + `apps/web/src/components/BindingEditor.tsx` — surface the questions. The component already exists for binding overrides; extend it rather than building new UI.

### Tests

- edtech fixture → exactly one question, about `score`/`revenue_amount`
- Answering "no" → run completes, revenue KPIs in `.skipped` with a readable reason
- Answering "yes" → run completes, binding marked `human_override`
- bookings.csv → **zero** questions (all used roles above threshold). If it produces questions, the threshold is too high — report rather than silently lowering it.
- Resume does not re-ask a confirmed binding
- `binding_confirmations={}` (declined) does not re-fire the pause

### Acceptance

- [ ] edtech generates successfully after the user declines the revenue binding
- [ ] The generated plugin contains no revenue metric and says so in SKILL.md's "not available" section
- [ ] bookings.csv is unchanged — clean, confident data still runs straight through
- [ ] Trace shows the binding agent now firing on low-confidence roles that previously bypassed it

**Commit:** `feat: gate low-confidence bindings behind human confirmation`

---

## P1-09 — Golden question eval harness

**Priority:** P0 (acceptance gate for the whole plan) · **Effort:** ~3d · **Depends on:** P1-04, P1-08

### Why this is the most important task in Phase 1

Every other change is a hypothesis until it is measured. There is currently no way to answer "did quality improve?" — the 183 existing tests verify mechanics, not whether the plugin answers questions correctly. All of P0.1–P1.6 in the review would have been caught by ~25 questions per fixture.

### Structure

```
evals/
  datasets/
    bookings/questions.yaml      # ~25
    edtech/questions.yaml        # ~25
    retail_orders/questions.yaml # ~25
    dirty_leads/questions.yaml   # ~15
  harness/
    __init__.py
    runner.py       # boot plugin over MCP stdio, drive a real model, capture transcript
    scoring.py      # deterministic scorers + one calibrated LLM judge
    report.py       # metrics table + JSON for CI comparison
  baselines/
    v0.json         # ★ record BEFORE Phase 1 lands, from a pre-P1 checkout
```

### Question schema

```yaml
- id: edtech_001
  question: "How many students are enrolled in each course?"
  category: multi_table
  ground_truth_sql: |
    SELECT c.course_name, COUNT(*) AS n
    FROM enrollments e JOIN courses c ON e.course_id = c.course_id
    GROUP BY c.course_name
  expects:
    tool: query_metric          # or get_kpi pre-Phase-2
    must_mention_by_name: true  # course NAMES, not course_id
    numeric_tolerance: 0

- id: edtech_007
  question: "What was our total revenue last quarter?"
  category: negative            # ★ weight these highest
  expects:
    behavior: refuse_or_clarify
    must_not: [returns_a_number]
    reason: >
      No revenue data exists in this dataset. Reporting sum(score) as revenue
      is a CRITICAL failure, not a partial-credit answer.
```

Categories: `single_table`, `multi_table`, `temporal`, `negative`, `ambiguous`, `robustness`.

**Author the negative cases first.** The failure that damages the product is a confident wrong answer, not a refusal.

### Metrics

| Metric | Definition | Target |
|---|---|---|
| `false_confidence_rate` | ★ wrong answers given **without** qualification | **< 1%** |
| `answer_accuracy` | numerically correct / answered | ≥ 98% |
| `capability_coverage` | answerable / total | ≥ 80% |
| `tool_selection_precision` | expected tool chosen first | ≥ 90% |
| `refusal_correctness` | negative questions correctly refused | ≥ 95% |
| `grounding_rate` | claims traceable to a tool result | 100% |
| `generation_cost` | tokens + wall seconds per plugin | tracked |

`false_confidence_rate` is the metric to run the programme on — it maps directly to commercial risk.

### Runner

Boot the generated plugin's MCP server over stdio exactly as Claude Desktop would (reuse `validation/mcp_smoke.py`'s session setup — do not reimplement). Drive a real model with the generated SKILL.md as system prompt and the real tool list. Capture the full transcript including tool calls.

Score deterministically wherever possible: numeric comparison, tool-name match, substring assertions. Use an LLM judge **only** for "was the answer appropriately qualified," and calibrate it against ~50 human labels before trusting it. Record the calibration in `evals/harness/judge_calibration.json`.

### CI integration

```bash
uv run python -m evals.harness.runner --dataset all --compare baselines/v0.json
```

Fail the build on regression in `false_confidence_rate` or `answer_accuracy`. Cost is roughly 500 model calls per full run — cheap enough for every generator change.

### Acceptance

- [ ] `v0.json` baseline recorded from a **pre-Phase-1** checkout
- [ ] Post-Phase-1 run shows measurable improvement in `false_confidence_rate` and `refusal_correctness`
- [ ] `edtech_007` (the revenue trap) passes after P1-08 and failed before it — this is the single clearest proof the plan is working
- [ ] Multi-table questions still fail, and the report says so plainly — that is P2-01's target

**Commit:** `test: add golden-question evaluation harness with v0 baseline`

---

# PHASE 2 — Capability

Do not start until Phase 1 is complete and the eval harness reports a baseline. These tasks carry more design latitude; the contracts are fixed, the implementation is yours.

---

## P2-01 — Entity graph and multi-table binding

**Priority:** P0 · **Effort:** L (2–3 weeks) · **Depends on:** Phase 1 complete

### The problem

`SchemaBindings.tables` holds a single `fact` alias. For edtech, `allowed_tables` is `["srcdb.\"enrollments\""]` — `students` and `courses` are unreachable by every tool including `run_safe_query`. So `enrollments_by_course` groups by the opaque `course_id`, and "which course is most popular?" answers `C003`.

Meanwhile `profiling/relationships.py` already detects FK candidates and verifies them by real value-overlap query — and **nothing downstream reads `StructuralProfile.relationships`.** You are one consumer away.

### Contract

New `models/entity_graph.py`:

```python
class JoinEdge(BaseModel):
    from_table: str; from_column: str
    to_table: str;   to_column: str
    cardinality: Literal["1:1", "N:1", "1:N", "N:N"]
    overlap_ratio: float
    orphan_ratio: float
    confidence: float
    origin: Literal["declared_fk", "value_overlap", "name_match"]
    fan_out_risk: bool     # ★ traversing this can duplicate measures

class Entity(BaseModel):
    name: str
    physical_table: str
    grain: TableGrain
    role: Literal["fact", "dimension", "bridge", "unknown"]
    key_columns: list[str]
    measures: list[str]
    dimensions: list[str]
    time_columns: list[str]
    row_count: int

class EntityGraph(BaseModel):
    entities: list[Entity]
    edges: list[JoinEdge]
    def join_path(self, a: str, b: str) -> list[JoinEdge] | None: ...
    def is_safe_to_aggregate(self, measure_entity: str, along: list[JoinEdge]) -> bool: ...
```

Fact/dimension classification is a **deterministic rule**, not a model call:

- **Dimension** — high grain confidence, single-column PK, low row count, few outbound FKs, mostly non-additive columns
- **Fact** — multiple outbound FKs to dimension PKs, additive numerics, ≥1 time column, high row count
- **Bridge** — ≥2 outbound FKs, composite key, few own measures

Add `detect_cardinality()` to `relationships.py` — one query per edge, checking whether child values repeat. **This is the guard against silently double-counted revenue and it is the most important new signal in this task.**

Extend `SchemaBindings.tables` to bind multiple aliases, and `allowed_tables` to the full join-reachable set.

### Fold in: grain-aware role classification

`profiling/structural.py::_guess_role` currently uses `cardinality <= max(1, min(30, row_count // 2))`. On a 4-row `courses` table, `course_name` (4 distinct) fails this and becomes `FREE_TEXT` → denied → **physically deleted**. Every genuine dimension table has `cardinality == row_count` by construction, so the heuristic is exactly inverted for the table class where it matters most.

Fix per review §5, and separately: **`FREE_TEXT` must never trigger deletion.** Deletion requires an explicit high-confidence PII signal. Exclude free text from projection if you like; do not destroy it.

### Acceptance

- [ ] retail_orders binds all 3 tables with correct fact/dimension roles
- [ ] `courses.course_name` survives generation
- [ ] Eval `capability_coverage` on multi-table questions rises from ~0% to >70%
- [ ] No metric traversing a fan-out edge is emitted

---

## P2-02 — Parameterized metric layer

**Priority:** P0 · **Effort:** M (1–2 weeks) · **Depends on:** P2-01

**The highest capability-per-effort change available.** You ship ~7 frozen SQL strings per plugin. Parameterizing over `{group_by, time_grain, filters}` yields roughly 400 answerable questions from ~20 metrics — with the **same seven tools** and the **same safety guarantees**.

Contract, rendering function, and validation rules: review §7 and §15.5. Non-negotiables:

- Every identifier comes from the validated model; every literal is a bound parameter. **Nothing from the user is ever string-interpolated into SQL.**
- `group_by` validated against `allowed_dimensions`; unknown → error naming the valid set
- `time_grain` validated against an enum
- Fan-out-unsafe dimensions rejected with a message explaining the double-count risk
- Final SQL still passes through `sqlglot.parse_one` and every existing runtime guard

Add `query_metric` to the runtime; keep `get_kpi` as a deprecated alias for one release.

- [ ] Coverage on "X by Y" questions rises sharply
- [ ] Tool count stays at 7

---

## P2-03 — Provenance on derived facts

**Priority:** P1 · **Effort:** M · **Depends on:** P2-02

Add `Provenance{origin, confidence, evidence, computed_by, computed_at, inputs}` to every derived fact (review §6). Two payoffs:

1. **Trustworthiness becomes a graph traversal.** Walk `inputs` transitively; if any ancestor is `inferred_llm` below τ and unconfirmed, the capability becomes an open question instead of a tool. Deterministic, testable, ~a day once provenance exists.
2. **The runtime can surface uncertainty.** Tool results carry `{confidence, caveats}` so Claude can say *"revenue was ₹84,200, though the revenue column was auto-detected and not confirmed."* This converts a silent failure mode into a visible one.

---

# PHASE 3 — Architecture

Only after Phase 2 ships and the eval harness shows sustained improvement. Full detail in review §§6, 7, 12, 13.

| ID | Task | Effort | Note |
|---|---|---|---|
| P3-01 | `SemanticModel` as single source of truth | L | The change that makes generation genuinely data-driven |
| P3-02 | Capability planner replaces pack-KPI enumeration | L | Capabilities emerge from evidence; packs become priors |
| P3-03 | LangGraph checkpointer + `interrupt()` + `Send` fan-out | M | **Infrastructure, not agents.** Fixes resume-recompute; makes the P1-08 gate durable; parallelises profiling. Do not add planner or specialist agents. |
| P3-04 | Fingerprint-based regeneration | M | Rename detection via distribution matching is the high-value piece |
| P3-05 | Sandboxed ingestion | M | DuckDB parses untrusted files in-process today |

---

## Appendix A — Things not to do

Recorded because each is a plausible-sounding move that would make the system worse.

1. **Do not convert `run_pipeline` into a `StateGraph` for its own sake.** A linear sequence of typed transformations is the right shape for a compiler. Adopt LangGraph only for the three capabilities in P3-03.
2. **Do not add a planner agent or specialist sub-agents.** Zero of the P0/P1 defects are orchestration problems. A planner agent would regenerate the same broken plugin with a nicer diagram.
3. **Do not generate per-customer tools.** The fixed seven-tool surface is a major asset: uniform tool-selection behaviour across every deployment. Capability scales through parameters (P2-02), not tool count.
4. **Do not emit per-customer Python.** `mcp-runtime` is byte-identical across plugins. Keep it that way — it is what makes patching and CVE response tractable.
5. **Do not relax a guardrail to make a test or a fixture pass.** If a pack KPI trips the new plausibility or PII checks, the pack is wrong.
6. **Do not let `self_critique` grow into the semantic validator.** It reviews generated *prose*; it structurally cannot see `schema_bindings.json`, where the real defects live. Semantic checks belong in deterministic harness checks (P1-04) and the eval harness (P1-09).
7. **Do not skip P1-09 because it is the least fun task.** Without it every other change is unfalsifiable.

## Appendix B — Files you will touch most

```
packages/forge-core/src/forge_core/
  orchestrator.py                 P1-03, P1-08 — the pipeline; keep it linear
  models/{bindings,run,industry_pack}.py   P1-01, P1-08
  binding/{resolver,scorer}.py    P1-08 — threshold change routes the existing agent
  binding/gate.py                 P1-08 — new
  validation/{harness,plausibility}.py     P1-04
  packaging/{denial,plugin_builder,redaction}.py  P1-03
  profiling/{structural,relationships,grain}.py   P2-01
  agentic/{memory,binding_agent}.py        P1-05, P1-07
  generation/hooks.py             P1-06

packages/mcp-runtime/src/mis_mcp_runtime/
  engine/{assertions,kpi_executor}.py      P1-01
  security/pii_policy.py          P1-02
  tools/, server.py               P2-02

evals/                            P1-09 — new tree
```
