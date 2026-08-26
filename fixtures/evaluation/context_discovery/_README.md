# Context Discovery golden fixtures

One directory per dataset, each pinning what the Context Discovery Agent
must find. Driven by `test_context_discovery_golden.py`, which runs every
fixture in this directory — adding a directory adds a benchmark, no test
change required.

Each `golden_context.json` splits its expectations in two:

- **`deterministic`** — reachable with no LLM key, asserted on every run.
  Entity keys, grain, question categories, and data-quality codes are all
  derived from measured structure, so they must hold for any dataset.
- **`semantic`** — needs a live agent (`domain`, the business process).
  Asserting these offline is what made the original edtech fixture
  tautological: it only passed because of a hardcoded `slug == "edtech"`
  keyword fallback, so it measured the fallback rather than the agent.

## A note on which enum gets asked about

Several fixtures have multiple columns that are structurally identical —
`bookings.status` and `bookings.gender` are both two-value label sets that
repeat across 20 rows. Nothing distinguishes them without reading the
column *name*, which this system deliberately does not do (see
`profiling/structural.py`). The deterministic floor therefore picks by a
stable rule (fewest distinct values, then name) and may well ask about
`gender` rather than `status`.

That is the honest cost of removing name heuristics, and it is bounded:
the worst case is one wasted question, never a wrong plugin. Choosing the
*semantically* right column is the LLM agent's job, and the agent runs by
default on every real run. These fixtures pin the floor, not the ceiling.
