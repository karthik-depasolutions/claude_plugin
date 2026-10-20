import Section from "../ui/Section";

const EXAMPLES: { canonical: string; physical: string }[] = [
  { canonical: "revenue_amount", physical: "amount_inr" },
  { canonical: "transaction_date", physical: "booking_date" },
  { canonical: "transaction_status", physical: "payment_status" },
];

export default function BindingThesis() {
  return (
    <Section tight divided>
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-center lg:gap-16">
        <div>
          <h2 className="font-display text-[26px] font-semibold leading-[1.15] tracking-[-0.02em] text-text sm:text-[32px]">
            Industry knowledge is data.
            <br />
            Your schema is a fact.
            <br />
            <span className="text-text-muted">They never touch.</span>
          </h2>
          <p className="mt-5 max-w-md text-[14px] leading-relaxed text-text-muted">
            A pack defines what &ldquo;revenue&rdquo; means once, in the abstract, never in terms of any
            one customer&rsquo;s columns. A deterministic binder maps that concept onto{" "}
            <span className="text-text">your</span> real column, for your dataset only. Nothing about one
            schema is ever hard-coded into what a KPI means.
          </p>
          <p className="mt-3 max-w-md text-[14px] leading-relaxed text-text-muted">
            That split is why the same engine produces a correct plugin for a three-column CSV and a
            forty-table warehouse without a rewrite for either.
          </p>
        </div>

        <div className="rounded-[var(--radius)] border border-border bg-surface p-5 sm:p-6">
          <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.12em] text-text-faint">
            <span>canonical role</span>
            <span>your column</span>
          </div>
          <ul className="mt-4 divide-y divide-border">
            {EXAMPLES.map((ex) => (
              <li key={ex.canonical} className="flex items-center gap-3 py-2.5 font-mono text-[13px]">
                <span className="text-text">{ex.canonical}</span>
                <span aria-hidden className="h-px flex-1 bg-border" />
                <span className="text-text-muted">{ex.physical}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 font-mono text-[11px] leading-relaxed text-text-faint">
            resolved by <span className="text-text-muted">binding/scorer.py</span>: token overlap and
            type compatibility, never guessed by a model.
          </p>
        </div>
      </div>
    </Section>
  );
}
