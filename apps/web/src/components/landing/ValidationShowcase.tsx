const CHECKS: { name: string; status: "pass" | "warn"; note: string }[] = [
  { name: "fact_check", status: "pass", note: "every bound column verified against the real schema" },
  { name: "sql_safety", status: "pass", note: "no SELECT *, no writes, table allow-list enforced" },
  { name: "dry_run", status: "pass", note: "every KPI actually executed against real data" },
  { name: "pii_scan", status: "pass", note: "denied columns absent from SQL and generated prose" },
  { name: "plugin_spec", status: "pass", note: "manifest, frontmatter, and hooks structurally valid" },
  { name: "cli_validate", status: "pass", note: "the real claude plugin validate --strict, not a mock" },
  { name: "mcp_smoke", status: "pass", note: "every tool called over a live stdio session" },
  { name: "self_critique", status: "warn", note: "a second LLM pass reviewing the first for hallucinated facts" },
];

const STATUS_STYLE: Record<"pass" | "warn", string> = {
  pass: "bg-physical/15 text-physical",
  warn: "bg-[#F5A623]/15 text-[#F5A623]",
};

export default function ValidationShowcase() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
      <div className="grid gap-14 lg:grid-cols-2 lg:gap-20">
        <div>
          <h2 className="font-display text-3xl font-medium tracking-tight text-paper sm:text-4xl">
            A model proposes.
            <br />
            It never ships unchecked.
          </h2>
          <p className="mt-6 max-w-md text-[15px] leading-relaxed text-muted">
            Every run passes through the same eight checks before packaging finishes, the same gate
            whether the source is a three-column CSV or a live production database. A hard failure means
            the plugin sits on disk for inspection; it doesn&rsquo;t reach an install command.
          </p>
        </div>

        <ul className="divide-y divide-line rounded-2xl border border-line bg-[#0E121B]">
          {CHECKS.map((check) => (
            <li key={check.name} className="flex items-start gap-4 px-5 py-4">
              <span
                className={`mt-0.5 shrink-0 rounded px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide ${STATUS_STYLE[check.status]}`}
              >
                {check.status}
              </span>
              <div className="min-w-0">
                <p className="font-mono text-sm text-paper">{check.name}</p>
                <p className="mt-0.5 text-xs text-muted">{check.note}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
