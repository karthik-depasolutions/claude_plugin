import Section from "../ui/Section";

const PHASES: { stages: string; title: string; body: string }[] = [
  {
    stages: "ingest → profile",
    title: "Point it at your data",
    body: "A CSV, a directory of tables, a SQLite file, or a live read-only Postgres connection. Every column is typed and profiled; direct identifiers and free text are marked for redaction before anything else runs.",
  },
  {
    stages: "classify → bind",
    title: "It works out the shape",
    body: "Column names and structure are scored against every industry pack. Below a confidence threshold it stops and asks a human. It never silently guesses the wrong industry.",
  },
  {
    stages: "compile → generate",
    title: "It builds only what's provable",
    body: "Each KPI compiles to real SQL against your bound columns. Anything that can't be resolved is skipped, not faked. Skills, agents, and commands are written from what actually compiled.",
  },
  {
    stages: "validate → package",
    title: "Nothing ships unchecked",
    body: "Eight checks have to pass, including the same claude CLI validator a real install hits, before the plugin is done rather than merely generated.",
  },
];

export default function HowItWorks() {
  return (
    <Section
      id="mechanism"
      divided
      eyebrow="The mechanism"
      title="Four phases. Same engine, every run."
    >
      <ol className="grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
        {PHASES.map((phase, i) => (
          <li key={phase.title} className="border-t border-border pt-4">
            <div className="flex items-center gap-2 font-mono text-[11px] text-text-faint">
              <span className="text-text-muted">{String(i + 1).padStart(2, "0")}</span>
              <span>{phase.stages}</span>
            </div>
            <h3 className="mt-3 font-display text-[16px] font-semibold tracking-[-0.01em] text-text">
              {phase.title}
            </h3>
            <p className="mt-2 text-[13px] leading-relaxed text-text-muted">{phase.body}</p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
