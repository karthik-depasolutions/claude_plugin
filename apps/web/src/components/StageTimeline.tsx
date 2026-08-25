import { Fragment, useEffect, useRef, useState } from "react";
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
  pending: { label: "Pending", className: "bg-line text-muted" },
  running: { label: "Running", className: "bg-canonical/15 text-canonical" },
  needs_input: { label: "Needs input", className: "bg-attention/15 text-attention" },
  succeeded: { label: "Succeeded", className: "bg-physical/15 text-physical" },
  failed: { label: "Failed", className: "bg-danger/15 text-danger" },
  cancelled: { label: "Cancelled", className: "bg-line text-muted" },
};

const HEADLINE: Record<RunStatus, string> = {
  pending: "Starting…",
  running: "Resolving your data…",
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
            <span key={i} className="rounded bg-line px-1.5 py-0.5 font-mono text-[11px] text-paper/80">
              {String(v)}
            </span>
          ))}
          {rest > 0 && <span className="self-center text-muted">+{rest} more</span>}
        </span>
      );
    }
    return (
      <span className="flex flex-col gap-0.5">
        {value.slice(0, 5).map((v, i) => (
          <span key={i} className="font-mono text-[11px] text-muted">
            {summarizeObject(v as Record<string, unknown>)}
          </span>
        ))}
        {value.length > 5 && <span className="text-muted">+{value.length - 5} more</span>}
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
    <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 font-mono text-[11px]">
      {entries.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="capitalize text-muted">{humanizeKey(key)}</dt>
          <dd className="min-w-0 text-paper/80">
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

/** The running-state marker: a conic-gradient ring, masked into a hairline
 * circle and spun continuously - physical (teal) leading into canonical
 * (violet) trailing, the same "resolving" idea as the sweep below, just
 * looping for as long as the stage is actually in flight. */
function ScanRing() {
  return (
    <div className="relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-canonical/10">
      <div
        className="animate-scan-spin absolute inset-0 rounded-full"
        style={{
          background:
            "conic-gradient(from 0deg, var(--color-physical), var(--color-canonical) 55%, transparent 62%, transparent 100%)",
          WebkitMask: "radial-gradient(farthest-side, transparent calc(100% - 2.5px), #000 calc(100% - 2.5px))",
          mask: "radial-gradient(farthest-side, transparent calc(100% - 2.5px), #000 calc(100% - 2.5px))",
        }}
      />
    </div>
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
  if (spinning) return <ScanRing />;
  const style = isFailed
    ? "bg-danger/15 text-danger ring-1 ring-danger/40"
    : isNeedsInput
      ? "bg-attention/15 text-attention ring-1 ring-attention/40"
      : isDone
        ? "bg-physical text-ink"
        : isCurrent
          ? "bg-canonical/10 text-canonical ring-1 ring-canonical/50"
          : "bg-transparent text-muted ring-1 ring-line";
  return (
    <div
      className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-medium transition-colors duration-300 ${style}`}
    >
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

  // Tracks which stage just transitioned to "done" so the resolve-sweep
  // plays exactly once for it, never replaying on an unrelated re-render
  // and never firing for stages that were already done on first paint
  // (e.g. reconnecting mid-run).
  const [justResolved, setJustResolved] = useState<RunStage | null>(null);
  const prevDoneCountRef = useRef(doneCount);
  useEffect(() => {
    if (doneCount > prevDoneCountRef.current) {
      const resolvedStage = STAGE_ORDER[doneCount - 1];
      setJustResolved(resolvedStage);
      const id = setTimeout(() => setJustResolved(null), 750);
      prevDoneCountRef.current = doneCount;
      return () => clearTimeout(id);
    }
    prevDoneCountRef.current = doneCount;
  }, [doneCount]);

  const eventsByStage = new Map<RunStage, StageEvent[]>();
  for (const event of events) {
    const list = eventsByStage.get(event.stage) ?? [];
    list.push(event);
    eventsByStage.set(event.stage, list);
  }

  const firstEventTime = events[0]?.timestamp;

  return (
    <div className="space-y-5 font-sans">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-base font-semibold tracking-tight text-paper">{HEADLINE[status]}</h2>
          <p className="mt-0.5 font-mono text-xs text-muted">
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
            className="rounded border border-line px-2 py-1 text-xs text-muted transition-colors hover:border-canonical/50 hover:text-paper"
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
          const isActiveScan = isCurrent && status === "running";
          return (
            <div key={stage} className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-line">
              <div
                className={`h-full rounded-full transition-[width,background-color] duration-500 ease-out ${
                  isDone
                    ? "w-full bg-physical"
                    : isCurrent
                      ? status === "failed"
                        ? "w-full bg-danger"
                        : status === "needs_input"
                          ? "w-full bg-attention"
                          : "w-1/2 bg-canonical"
                      : "w-0"
                }`}
              />
              {isActiveScan && (
                <div
                  className="animate-progress-scan absolute inset-y-0 left-0 w-full"
                  style={{
                    background: "linear-gradient(90deg, transparent, var(--color-physical) 45%, transparent 65%)",
                  }}
                />
              )}
            </div>
          );
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
          const sweeping = justResolved === stage;

          const start = stageEvents[0]?.timestamp;
          const nextStart = eventsByStage.get(STAGE_ORDER[index + 1])?.[0]?.timestamp;
          const staticEnd = nextStart ?? (isLast ? stageEvents[stageEvents.length - 1]?.timestamp : undefined);

          return (
            <li key={stage} className="relative flex gap-3 overflow-hidden">
              {sweeping && (
                <div
                  className="animate-resolve-sweep pointer-events-none absolute inset-y-0 left-0 w-2/3"
                  style={{
                    background:
                      "linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-canonical) 35%, transparent), color-mix(in srgb, var(--color-physical) 35%, transparent), transparent)",
                  }}
                />
              )}
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
                  <div
                    className={`min-h-6 w-px flex-1 transition-colors duration-500 ${isDone ? "bg-physical/50" : "bg-line"}`}
                  />
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
                  <span className="font-display text-sm font-medium text-paper">{LABELS[stage]}</span>
                  {!isDone && !isCurrent && <span className="text-xs text-muted">pending</span>}
                  {isCurrent && !isFailed && !isNeedsInput && (
                    <span className="text-xs text-canonical">resolving…</span>
                  )}
                  <span className="flex-1" />
                  {isSpinning && start && (
                    <span className="font-mono text-xs text-muted">
                      <LiveDuration startIso={start} />
                    </span>
                  )}
                  {!isSpinning && start && staticEnd && staticEnd !== start && (
                    <span className="font-mono text-xs text-muted">
                      {formatDuration(new Date(staticEnd).getTime() - new Date(start).getTime())}
                    </span>
                  )}
                  {showDetails && stageEvents.length > 0 && (
                    <span className="text-muted">
                      <ChevronIcon open={isOpen} />
                    </span>
                  )}
                </button>

                {!isOpen && latestMessage && <p className="mt-0.5 truncate text-xs text-muted">{latestMessage}</p>}

                {isOpen && (
                  <ul className="mt-1.5 space-y-2 border-l border-line pl-3">
                    {stageEvents.map((event, i) => (
                      <li key={i}>
                        <div className="flex gap-2 text-xs">
                          <span className="shrink-0 font-mono tabular-nums text-muted/70">
                            {formatTime(event.timestamp)}
                          </span>
                          <span className="text-paper/80">{event.message}</span>
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
