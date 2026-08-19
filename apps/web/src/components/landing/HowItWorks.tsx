const PHASES: { stages: string; title: string; body: string }[] = [
  {
    stages: "ingest → profile",
    title: "Point it at your data",
    body: "A CSV, a directory of tables, a SQLite file, or a live read-only Postgres connection. Every column gets typed, profiled, and scanned for likely PII before anything else happens.",
  },
  {
    stages: "classify → bind",
    title: "It figures out the shape",
    body: "Column names and structure are scored against every industry pack. Below a confidence threshold, it stops and asks a human — it never silently guesses the wrong industry.",
  },
  {
    stages: "compile → generate",
    title: "It builds only what's provable",
    body: "Each KPI a pack defines gets compiled to real SQL against your bound columns. Anything that can't be resolved is skipped, not faked — then skills, agents, and commands are written from what actually compiled.",
  },
  {
    stages: "validate → package",
    title: "Nothing ships unchecked",
    body: "Eight checks run — including the same claude CLI validator a human install would hit. A hard failure keeps the plugin on disk for inspection; it doesn't reach an install command.",
  },
];

export default function HowItWorks() {
  return (
    <section className="border-y border-line bg-[#0A0D13] py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">The pipeline, zoomed out</p>
        <h2 className="mt-4 max-w-xl font-display text-3xl font-medium tracking-tight text-paper sm:text-4xl">
          Four phases. Same engine, every time.
        </h2>

        <ol className="mt-16 grid gap-x-8 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
          {PHASES.map((phase, i) => (
            <li key={phase.title} className="relative pl-0">
              <div className="flex items-center gap-3 border-t border-canonical/40 pt-5">
                <span className="font-mono text-xs text-canonical">{String(i + 1).padStart(2, "0")}</span>
                <span className="font-mono text-[11px] text-muted">{phase.stages}</span>
              </div>
              <h3 className="mt-4 font-display text-lg font-medium text-paper">{phase.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{phase.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
