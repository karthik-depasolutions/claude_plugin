import { Fragment, useEffect, useState } from "react";
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

const STATUS_META: Record<RunStatus, { label: string; className: string }> = {
  pending: { label: "Pending", className: "bg-slate-700 text-slate-300" },
  running: { label: "Running", className: "bg-sky-500/20 text-sky-400" },
  needs_input: { label: "Needs input", className: "bg-amber-500/20 text-amber-400" },
  succeeded: { label: "Succeeded", className: "bg-emerald-500/20 text-emerald-400" },
  failed: { label: "Failed", className: "bg-red-500/20 text-red-400" },
  cancelled: { label: "Cancelled", className: "bg-slate-700 text-slate-400" },
};

const HEADLINE: Record<RunStatus, string> = {
  pending: "Starting…",
  running: "Generating your plugin…",
  needs_input: "Needs your input",
  succeeded: "Plugin generated",
  failed: "Generation failed",
  cancelled: "Run cancelled",
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

function formatDuration(ms: number): string {
  if (ms < 1000) return "<1s";
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(totalSeconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

/** Ticks its own state every second rather than lifting the interval into the
 * parent, so a live counter doesn't force the whole stage list to re-render. */
function LiveDuration({ startIso }: { startIso: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const ms = Date.now() - new Date(startIso).getTime();
  return <span className="tabular-nums">{formatDuration(Math.max(ms, 0))}</span>;
}

function humanizeKey(key: string): string {
  return key.replace(/_/g, " ");
}

function summarizeObject(obj: Record<string, unknown>): string {
  return Object.entries(obj)
    .filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0))
    .slice(0, 4)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
    .join(" · ");
}

function DataValue({ value }: { value: unknown }): React.ReactElement {
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "number" || typeof value === "string") return <>{String(value)}</>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <>—</>;
    if (value.every((v) => typeof v === "string" || typeof v === "number")) {
      const shown = value.slice(0, 8);
      const rest = value.length - shown.length;
      return (
        <span className="flex flex-wrap gap-1">
          {shown.map((v, i) => (
            <span key={i} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">
              {String(v)}
            </span>
          ))}
          {rest > 0 && <span className="self-center text-slate-500">+{rest} more</span>}
        </span>
      );
    }
    return (
      <span className="flex flex-col gap-0.5">
        {value.slice(0, 5).map((v, i) => (
          <span key={i} className="font-mono text-[11px] text-slate-400">
            {summarizeObject(v as Record<string, unknown>)}
          </span>
        ))}
        {value.length > 5 && <span className="text-slate-500">+{value.length - 5} more</span>}
      </span>
    );
  }
  if (typeof value === "object") return <>{summarizeObject(value as Record<string, unknown>)}</>;
  return <>{String(value)}</>;
}

/** Renders an event's `data` payload as compact key/value rows. Skips
 * `report` - the validate stage's full ValidationReport is already rendered
 * by ValidationReportView elsewhere on the page, so repeating it here would
 * just be noise. */
function EventDataPreview({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([key, value]) =>
      key !== "report" && value !== null && value !== undefined && !(Array.isArray(value) && value.length === 0)
  );
  if (entries.length === 0) return null;
  return (
    <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="capitalize text-slate-500">{humanizeKey(key)}</dt>
          <dd className="min-w-0 text-slate-300">
            <DataValue value={value} />
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className={`h-3.5 w-3.5 shrink-0 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path d="M5 7.5 10 12.5 15 7.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function StepBadge({
  index,
  isDone,
  isCurrent,
  isFailed,
  isNeedsInput,
  spinning,
}: {
  index: number;
  isDone: boolean;
  isCurrent: boolean;
  isFailed: boolean;
  isNeedsInput: boolean;
  spinning: boolean;
}) {
  if (spinning) {
    return <div className="h-6 w-6 animate-spin rounded-full border-2 border-sky-500/30 border-t-sky-400" />;
  }
  const style = isFailed
    ? "bg-red-500/20 text-red-400 ring-1 ring-red-500/40"
    : isNeedsInput
      ? "bg-amber-500/20 text-amber-400 ring-1 ring-amber-500/40"
      : isDone
        ? "bg-emerald-500/20 text-emerald-400"
        : isCurrent
          ? "bg-sky-500/20 text-sky-400 ring-1 ring-sky-500/40"
          : "bg-slate-800 text-slate-500";
  return (
    <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${style}`}>
      {isFailed ? "!" : isNeedsInput ? "?" : isDone ? "✓" : index + 1}
    </div>
  );
}

export default function StageTimeline({ events, status, currentStage }: Props) {
  const [showDetails, setShowDetails] = useState(true);
  const [expanded, setExpanded] = useState<Set<RunStage>>(() => new Set(currentStage ? [currentStage] : []));

  // Auto-expand a newly-reached stage (and always keep a stuck/failed one
  // visible) without fighting a user's manual collapse of earlier stages.
  useEffect(() => {
    if (!currentStage) return;
    setExpanded((prev) => (prev.has(currentStage) ? prev : new Set(prev).add(currentStage)));
  }, [currentStage, status]);

  const reachedIndex = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;
  const doneCount = status === "succeeded" ? STAGE_ORDER.length : Math.max(reachedIndex, 0);
  const stepNumber = Math.min(doneCount + (status === "running" ? 1 : 0), STAGE_ORDER.length);

  const eventsByStage = new Map<RunStage, StageEvent[]>();
  for (const event of events) {
    const list = eventsByStage.get(event.stage) ?? [];
    list.push(event);
    eventsByStage.set(event.stage, list);
  }

  const firstEventTime = events[0]?.timestamp;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">{HEADLINE[status]}</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Step {stepNumber} of {STAGE_ORDER.length}
            {firstEventTime && (status === "running" || status === "pending") && (
              <>
                {" "}
                · <LiveDuration startIso={firstEventTime} />
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowDetails((v) => !v)}
            className="rounded border border-slate-800 px-2 py-1 text-xs text-slate-400 transition-colors hover:border-slate-700 hover:text-slate-200"
          >
            {showDetails ? "Hide details" : "Show details"}
          </button>
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_META[status].className}`}>
            {STATUS_META[status].label}
          </span>
        </div>
      </div>

      <div className="flex gap-1">
        {STAGE_ORDER.map((stage, index) => {
          const isDone = index < reachedIndex || (index === reachedIndex && status === "succeeded");
          const isCurrent = index === reachedIndex && status !== "succeeded";
          const color = isCurrent
            ? status === "failed"
              ? "bg-red-500"
              : status === "needs_input"
                ? "bg-amber-500"
                : "bg-sky-500 animate-pulse"
            : isDone
              ? "bg-emerald-500"
              : "bg-slate-800";
          return <div key={stage} className={`h-1.5 flex-1 rounded-full transition-colors duration-300 ${color}`} />;
        })}
      </div>

      <ol>
        {STAGE_ORDER.map((stage, index) => {
          const isDone = index < reachedIndex || (index === reachedIndex && status === "succeeded");
          const isCurrent = index === reachedIndex && status !== "succeeded";
          const isFailed = isCurrent && status === "failed";
          const isNeedsInput = isCurrent && status === "needs_input";
          const isSpinning = isCurrent && status === "running";
          const isLast = index === STAGE_ORDER.length - 1;
          const stageEvents = eventsByStage.get(stage) ?? [];
          const latestMessage = stageEvents[stageEvents.length - 1]?.message;
          const isOpen = showDetails && expanded.has(stage) && stageEvents.length > 0;

          const start = stageEvents[0]?.timestamp;
          const nextStart = eventsByStage.get(STAGE_ORDER[index + 1])?.[0]?.timestamp;
          const staticEnd = nextStart ?? (isLast ? stageEvents[stageEvents.length - 1]?.timestamp : undefined);

          return (
            <li key={stage} className="flex gap-3">
              <div className="flex flex-col items-center">
                <StepBadge
                  index={index}
                  isDone={isDone}
                  isCurrent={isCurrent}
                  isFailed={isFailed}
                  isNeedsInput={isNeedsInput}
                  spinning={isSpinning}
                />
                {!isLast && (
                  <div className={`min-h-6 w-px flex-1 ${isDone ? "bg-emerald-600/40" : "bg-slate-800"}`} />
                )}
              </div>

              <div className="min-w-0 flex-1 pb-4">
                <button
                  type="button"
                  disabled={stageEvents.length === 0}
                  onClick={() =>
                    setExpanded((prev) => {
                      const next = new Set(prev);
                      if (next.has(stage)) next.delete(stage);
                      else next.add(stage);
                      return next;
                    })
                  }
                  className="flex w-full items-baseline gap-2 text-left disabled:cursor-default"
                >
                  <span className="text-sm font-medium text-slate-200">{LABELS[stage]}</span>
                  {!isDone && !isCurrent && <span className="text-xs text-slate-500">pending</span>}
                  {isCurrent && !isFailed && !isNeedsInput && <span className="text-xs text-sky-400">in progress…</span>}
                  <span className="flex-1" />
                  {isSpinning && start && (
                    <span className="text-xs text-slate-500">
                      <LiveDuration startIso={start} />
                    </span>
                  )}
                  {!isSpinning && start && staticEnd && staticEnd !== start && (
                    <span className="text-xs text-slate-500">
                      {formatDuration(new Date(staticEnd).getTime() - new Date(start).getTime())}
                    </span>
                  )}
                  {showDetails && stageEvents.length > 0 && (
                    <span className="text-slate-500">
                      <ChevronIcon open={isOpen} />
                    </span>
                  )}
                </button>

                {!isOpen && latestMessage && <p className="mt-0.5 truncate text-xs text-slate-400">{latestMessage}</p>}

                {isOpen && (
                  <ul className="mt-1.5 space-y-2 border-l border-slate-800/80 pl-3">
                    {stageEvents.map((event, i) => (
                      <li key={i}>
                        <div className="flex gap-2 text-xs">
                          <span className="shrink-0 tabular-nums text-slate-600">{formatTime(event.timestamp)}</span>
                          <span className="text-slate-300">{event.message}</span>
                        </div>
                        <EventDataPreview data={event.data} />
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
