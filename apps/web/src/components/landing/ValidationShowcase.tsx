import Section from "../ui/Section";
import { StatusGlyph } from "../ui/StatusBadge";

const CHECKS: { name: string; status: "pass" | "warn"; note: string }[] = [
  { name: "fact_check", status: "pass", note: "every bound column verified against the real schema" },
  { name: "sql_safety", status: "pass", note: "no SELECT *, no writes, table allow-list enforced" },
  { name: "dry_run", status: "pass", note: "every KPI actually executed against real data" },
  { name: "plugin_spec", status: "pass", note: "manifest, frontmatter, and hooks structurally valid" },
  { name: "cli_validate", status: "pass", note: "the real claude plugin validate --strict, not a mock" },
  { name: "mcp_smoke", status: "pass", note: "every tool called over a live stdio session" },
  { name: "self_critique", status: "warn", note: "a second model pass reviewing the first for hallucinated facts" },
  { name: "schema_model", status: "pass", note: "knowledge pack grounded: every table, column and query is real" },
];

export default function ValidationShowcase() {
  return (
    <Section
      id="validation"
      divided
      eyebrow="The gate"
      title="A model proposes. It never ships unchecked."
      lede="Every run passes the same eight checks before packaging finishes — the same gate for a three-column CSV or a live production database. A hard failure leaves the plugin on disk for inspection; it never reaches an install command."
    >
      <div className="grid gap-x-10 gap-y-5 rounded-[var(--radius)] border border-border bg-surface p-5 sm:grid-cols-2 sm:p-6">
        {CHECKS.map((c) => (
          <div key={c.name} className="flex gap-3">
            <span className="mt-0.5">
              <StatusGlyph status={c.status} />
            </span>
            <div className="min-w-0">
              <p className="font-mono text-[12.5px] text-text">{c.name}</p>
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-text-muted">{c.note}</p>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}
