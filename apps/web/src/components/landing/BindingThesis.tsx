const EXAMPLES: { canonical: string; physical: string }[] = [
  { canonical: "revenue_amount", physical: "amount_inr" },
  { canonical: "transaction_date", physical: "booking_date" },
  { canonical: "transaction_status", physical: "payment_status" },
];

export default function BindingThesis() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
      <div className="grid gap-14 lg:grid-cols-2 lg:items-center lg:gap-20">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">The actual mechanism</p>
          <h2 className="mt-4 font-display text-3xl font-medium tracking-tight text-paper sm:text-4xl">
            Industry knowledge is data.
            <br />
            Your schema is a fact.
            <br />
            <span className="text-muted">They never touch.</span>
          </h2>
          <p className="mt-6 max-w-md text-[15px] leading-relaxed text-muted">
            An industry pack defines what &ldquo;revenue&rdquo; means once, in the abstract — never in terms
            of any one customer&rsquo;s column names. A deterministic binder maps that concept onto{" "}
            <em className="text-paper not-italic">your</em> real column, for your dataset only. Nothing about
            one customer&rsquo;s schema is ever hard-coded into what a KPI means.
          </p>
          <p className="mt-4 max-w-md text-[15px] leading-relaxed text-muted">
            That split is why the same engine produces a correct plugin for a three-column CSV and a
            forty-table warehouse without being rewritten for either.
          </p>
        </div>

        <div className="rounded-2xl border border-line bg-[#0E121B] p-6 sm:p-8">
          <div className="flex items-center justify-between font-mono text-[11px] uppercase tracking-wide text-muted">
            <span>canonical role</span>
            <span>your physical column</span>
          </div>
          <ul className="mt-4 space-y-3">
            {EXAMPLES.map((ex) => (
              <li
                key={ex.canonical}
                className="flex items-center justify-between gap-3 rounded-lg border border-line/80 bg-ink/60 px-4 py-3 font-mono text-sm"
              >
                <span className="text-canonical">{ex.canonical}</span>
                <span aria-hidden className="flex-1 border-t border-dashed border-line" />
                <span className="text-physical">{ex.physical}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 font-mono text-xs text-muted">
            resolved by <span className="text-paper">binding/scorer.py</span> — token overlap + type
            compatibility, never guessed by an LLM.
          </p>
        </div>
      </div>
    </section>
  );
}
