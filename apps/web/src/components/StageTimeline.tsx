import { STAGE_ORDER, type RunStage, type RunStatus, type StageEvent } from "../lib/types";

const LABELS: Record<RunStage, string> = {
  ingest: "Ingest data",
  profile: "Profile schema",
  classify: "Classify industry",
  bind: "Bind schema",
  compile_kpis: "Compile KPIs",
  generate: "Generate content",
  package: "Package plugin",
  validate: "Validate",
  publish: "Publish",
};

interface Props {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour12: false });
}

export default function StageTimeline({ events, status, currentStage }: Props) {
  const reachedIndex = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;
  const doneCount =
    status === "succeeded" ? STAGE_ORDER.length : Math.max(reachedIndex, 0);
  const eventsByStage = new Map<RunStage, StageEvent[]>();
  for (const event of events) {
    const list = eventsByStage.get(event.stage) ?? [];
    list.push(event);
    eventsByStage.set(event.stage, list);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span>
          Step {Math.min(doneCount + (status === "running" ? 1 : 0), STAGE_ORDER.length)} of{" "}
          {STAGE_ORDER.length}
        </span>
        <span className="uppercase tracking-wide">{status.replace("_", " ")}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-sky-500 transition-all duration-300"
          style={{ width: `${(doneCount / STAGE_ORDER.length) * 100}%` }}
        />
      </div>

      <ol className="space-y-2">
        {STAGE_ORDER.map((stage, index) => {
          const isDone = index < reachedIndex || (index === reachedIndex && status === "succeeded");
          const isCurrent = index === reachedIndex && status !== "succeeded";
          const isFailed = isCurrent && status === "failed";
          const isNeedsInput = isCurrent && status === "needs_input";
          const stageEvents = eventsByStage.get(stage) ?? [];

          return (
            <li key={stage} className="flex items-start gap-3 text-sm">
              <span
                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs ${
                  isFailed
                    ? "bg-red-500/20 text-red-400"
                    : isNeedsInput
                      ? "bg-amber-500/20 text-amber-400"
                      : isDone
                        ? "bg-emerald-500/20 text-emerald-400"
                        : isCurrent
                          ? "animate-pulse bg-sky-500/20 text-sky-400"
                          : "bg-slate-800 text-slate-500"
                }`}
              >
                {isFailed ? "!" : isNeedsInput ? "?" : isDone ? "✓" : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="font-medium text-slate-200">{LABELS[stage]}</span>
                  {!isDone && !isCurrent && <span className="text-xs text-slate-500">pending</span>}
                  {isCurrent && !isFailed && !isNeedsInput && (
                    <span className="text-xs text-sky-400">in progress…</span>
                  )}
                </div>
                {stageEvents.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {stageEvents.map((event, i) => (
                      <li key={i} className="flex gap-2 text-xs text-slate-400">
                        <span className="shrink-0 tabular-nums text-slate-600">
                          {formatTime(event.timestamp)}
                        </span>
                        <span>{event.message}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
