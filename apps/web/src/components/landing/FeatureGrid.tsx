const FEATURES: { title: string; body: string }[] = [
  {
    title: "Industry packs",
    body: "Healthcare, retail, finance, edtech, and a no-assumptions generic fallback. Each one is a folder of canonical KPI definitions, not a branch of code.",
  },
  {
    title: "Deterministic SQL",
    body: "Every KPI compiles to sqlglot-validated SQL. The model proposes column bindings and prose; it never ships SQL that runs unverified.",
  },
  {
    title: "8-check validation gate",
    body: "Schema fact-check, SQL safety, a real dry-run, a PII scan, plugin-spec validation, the actual claude CLI validator, an MCP smoke test, and an LLM self-critique, all before anything packages.",
  },
  {
    title: "A real MCP tool surface",
    body: "describe_schema, get_kpi, run_safe_query, render_chart: the same tools Claude calls at install time, guarded by a table allow-list and row/timeout limits.",
  },
  {
    title: "PII stays out, by construction",
    body: "Denied columns are identified and stripped before packaging on every run, not as a checklist step someone can forget.",
  },
  {
    title: "Publish in one step",
    body: "A brand-new GitHub repo per plugin, installable in two commands. No shared marketplace repo required first.",
  },
];

export default function FeatureGrid() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
      <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">What's actually in it</p>
      <h2 className="mt-4 max-w-xl font-display text-3xl font-medium tracking-tight text-paper sm:text-4xl">
        Not a wrapper around a prompt.
      </h2>

      <div className="mt-14 grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title} className="group bg-ink p-7 transition-colors hover:bg-[#0E121B]">
            <h3 className="font-display text-base font-medium text-paper">{f.title}</h3>
            <p className="mt-2.5 text-sm leading-relaxed text-muted">{f.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
