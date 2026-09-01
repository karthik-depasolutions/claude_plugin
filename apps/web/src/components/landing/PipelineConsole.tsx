import { useEffect, useState } from "react";

/** Mirrors STAGE_ORDER in lib/types.ts exactly: this is the real 8-stage
 * pipeline every run goes through, not an invented marketing sequence.
 * The detail strings are from a real bookings.csv run. */
const STAGES: { label: string; detail?: string }[] = [
  { label: "Ingest data", detail: "1 table detected" },
  { label: "Profile schema", detail: "17 columns" },
  { label: "Classify industry", detail: "healthcare-diagnostics" },
  { label: "Bind schema", detail: "9 bound, 0 unresolved" },
  { label: "Compile KPIs", detail: "7/7 compiled" },
  { label: "Generate content", detail: "skill, agent, commands" },
  { label: "Package plugin", detail: "spec-checked" },
  { label: "Validate", detail: "8 checks · pass" },
];

const STEP_DELAY_MS = 260;

export default function PipelineConsole() {
  const [revealed, setRevealed] = useState(0);

  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      setRevealed(STAGES.length);
      return;
    }
    const timers = STAGES.map((_, i) =>
      window.setTimeout(() => setRevealed((r) => Math.max(r, i + 1)), 500 + i * STEP_DELAY_MS)
    );
    return () => timers.forEach(window.clearTimeout);
  }, []);

  return (
    <div className="w-full max-w-sm rounded-xl border border-line bg-[#0E121B] font-mono text-[13px] shadow-2xl shadow-black/40">
      <div className="flex items-center gap-1.5 border-b border-line px-4 py-3">
        <span className="h-2.5 w-2.5 rounded-full bg-[#FB5B5B]/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#F5A623]/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-physical/70" />
        <span className="ml-2 text-xs text-muted">forge run bookings.csv</span>
      </div>
      <ol className="space-y-2.5 px-4 py-4">
        {STAGES.map((stage, i) => {
          const isDone = i < revealed;
          return (
            <li
              key={stage.label}
              className={`flex items-baseline justify-between gap-3 ${isDone ? "animate-stage-check" : "opacity-0"}`}
            >
              <span className="flex items-baseline gap-2">
                <span className={isDone ? "text-physical" : "text-muted"}>{isDone ? "✓" : "·"}</span>
                <span className={isDone ? "text-paper" : "text-muted"}>{stage.label}</span>
              </span>
              {isDone && stage.detail && <span className="truncate text-muted">{stage.detail}</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
