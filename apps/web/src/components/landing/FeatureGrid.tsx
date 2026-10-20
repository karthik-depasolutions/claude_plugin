import DefinitionList from "../ui/DefinitionList";
import Section from "../ui/Section";

const FEATURES: { title: string; body: string }[] = [
  {
    title: "Industry packs",
    body: "Healthcare, retail, finance, edtech, and a no-assumptions generic fallback. Each is a folder of canonical KPI definitions, not a branch of code.",
  },
  {
    title: "Deterministic SQL",
    body: "Every KPI compiles to sqlglot-validated SQL. The model proposes column bindings and prose; it never ships SQL that runs unverified.",
  },
  {
    title: "8-check validation gate",
    body: "Schema fact-check, SQL safety, a real dry-run, plugin-spec validation, the actual claude CLI validator, an MCP smoke test, an LLM self-critique, and a grounded-knowledge-pack check — all before packaging.",
  },
  {
    title: "A real MCP tool surface",
    body: "describe_schema, get_kpi, run_safe_query, render_chart — the same tools Claude calls at install time, behind a table allow-list and row/timeout limits.",
  },
  {
    title: "Identifiers stay out by construction",
    body: "Columns whose structural role is email, phone, or free text are excluded from every query, dropped from the bundled data, and kept out of the knowledge pack. Role-based redaction in code, not a checklist step.",
  },
  {
    title: "Publish in one step",
    body: "A brand-new GitHub repo per plugin, installable in two commands. No shared marketplace repo required first.",
  },
];

export default function FeatureGrid() {
  return (
    <Section
      id="whats-in-it"
      divided
      eyebrow="What's in it"
      title="Not a wrapper around a prompt."
    >
      <DefinitionList
        termFont="sans"
        termWidth="sm:w-64"
        rows={FEATURES.map((f) => ({ term: f.title, detail: f.body }))}
      />
    </Section>
  );
}
