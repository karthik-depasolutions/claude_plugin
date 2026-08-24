# ADR 0001: Single-fact-table scope for the entity graph (P2-00)

**Status:** Accepted
**Decides:** P2-01's `EntityGraph`/`SchemaBindings` scope going forward.

## Question

Does any industry pack's KPI set ever need more than one fact table in a
single plugin? `SchemaBindings.tables` is typed as `list[TableBinding]` but
`resolve_bindings()` has only ever populated exactly one `"fact"` alias
(`binding/resolver.py:343`). Before building the entity graph (P2-01), P2-00
asks whether that's an accidental gap or the correct scope.

## Investigation

Every canonical-role token referenced across every pack's KPI SQL
(`grep -ho '{{[a-z_]*}}' industry-packs/*/kpis/*.json`):

```
edtech:                {{fact}} {{course_ref}} {{score}} {{transaction_date}} {{transaction_status}} ...
finance:                {{fact}} {{revenue_amount}} {{transaction_date}} {{transaction_status}} {{transaction_type}} ...
generic-analytics:      {{fact}} {{category_dim}} {{date_dim}} {{measure_amount}}
healthcare-diagnostics: {{fact}} {{customer_ref}} {{location}} {{partner_name}} {{product_name}} {{revenue_amount}} ...
retail-ecommerce:       {{fact}} {{location}} {{payment_channel}} {{revenue_amount}} {{transaction_date}} ...
```

Every pack references exactly one table token, `{{fact}}`. No pack
references `fact_2`, `dim_*`, or any second table token
(`grep -rl 'fact_2\|dim_\|{{[a-z_]*_table}}' industry-packs/` matches
nothing). Every other token (`course_ref`, `location`, `partner_name`, ...)
is a *column role* on that one fact table, not a second table.

This matches the review's own framing exactly: the businesses these five
packs model (a booking, an enrollment, an order, a transaction) each have
one natural grain-defining event table, with everything else (customer,
course, location, partner) reachable as a dimension joined off it.

## Decision

**(a) Single fact + dimensions.** P2-01's `EntityGraph` scopes to one fact
entity per plugin, with `role: Literal["fact", "dimension", "bridge",
"unknown"]` classifying every other bound table relative to it.
`SchemaBindings.tables` stays a list (multiple dimension aliases now bind
into it - that's the actual point of P2-01), but only one entry may ever
carry `role="fact"`.

This is not a permanent architectural ceiling - a plugin spanning two
independent event streams (`orders` **and** `support_tickets`) is a real
scenario eventually, and multi-fact support can be reached later by
generating multiple bound semantic models within one plugin rather than by
redesigning `EntityGraph` itself. But no pack, dataset, or reported customer
need in this repository requires it today, and (per the review's own
argument) a semantic layer answering ~400 questions from a single verified
fact table is valuable well before that problem needs solving. Building for
it now would be speculative complexity against zero real evidence.

## Consequence for P2-01

- `EntityGraph.entities` may contain many entities; deterministic
  fact/dimension/bridge classification (review §5, PHASE_2.md P2-01) picks
  exactly one `fact`.
- `join_path(a, b)` only ever needs to route through that one fact table or
  between its dimensions - no cross-fact join planning required.
- If classification ever produces zero or more-than-one high-confidence
  fact candidate on a real dataset, that's a signal to revisit this ADR,
  not a case to silently force-resolve.
