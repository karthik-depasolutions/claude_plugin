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

const STAGE_SUB_HINTS: Record<RunStage, string> = {
  ingest: "Connecting to data source and loading tables into workspace",
  profile: "Statistical data-map, entropy, value frequency & AI reasoning",
  classify: "Evaluating schema structure against industry domain ontology",
  bind: "Mapping physical columns to semantic metric roles with AST safety",
  compile_kpis: "Compiling business formulas, aggregates, and metric definitions",
  generate: "Authoring plugin manifest, MCP tool handlers & operational skills",
  package: "Bundling plugin artifacts, SQLite caches and configurations",
  validate: "Running comprehensive sandbox verification and smoke tests",
  publish: "Packaging distribution ready bundle",
};

const STATUS_META: Record<RunStatus, { label: string; className: string }> = {
  pending: { label: "Pending", className: "bg-line text-muted" },
  running: { label: "Running", className: "bg-canonical/20 text-canonical border border-canonical/40" },
  needs_input: { label: "Needs input", className: "bg-attention/20 text-attention border border-attention/40" },
  succeeded: { label: "Succeeded", className: "bg-physical/20 text-physical border border-physical/40" },
  failed: { label: "Failed", className: "bg-danger/20 text-danger border border-danger/40" },
  cancelled: { label: "Cancelled", className: "bg-line text-muted" },
};

const HEADLINE: Record<RunStatus, string> = {
  pending: "Starting Pipeline…",
  running: "AI Agent Active · Resolving your data",
  needs_input: "Action Required · Clarify Business Context",
  succeeded: "MIS Plugin Ready",
  failed: "Generation Encountered an Error",
  cancelled: "Run Cancelled",
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

function ScanRing() {
  return (
    <div className="relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-canonical/15">
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
        ? "bg-physical text-ink font-bold"
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
  const [showLiveFeed, setShowLiveFeed] = useState(true);
  const [expanded, setExpanded] = useState<Set<RunStage>>(() => new Set(currentStage ? [currentStage] : []));
  const feedScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!currentStage) return;
    setExpanded((prev) => (prev.has(currentStage) ? prev : new Set(prev).add(currentStage)));
  }, [currentStage, status]);

  // Auto-scroll the live feed as new events arrive
  useEffect(() => {
    if (feedScrollRef.current) {
      feedScrollRef.current.scrollTop = feedScrollRef.current.scrollHeight;
    }
  }, [events.length]);

  const reachedIndex = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;
  const doneCount = status === "succeeded" ? STAGE_ORDER.length : Math.max(reachedIndex, 0);
  const stepNumber = Math.min(doneCount + (status === "running" ? 1 : 0), STAGE_ORDER.length);

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
  const latestEvent = events[events.length - 1];

  return (
    <div className="space-y-6 font-sans">
      {/* Top Banner & Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-display text-lg font-bold tracking-tight text-paper">{HEADLINE[status]}</h2>
            {status === "running" && (
              <span className="flex h-2 w-2 relative">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-physical opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-physical" />
              </span>
            )}
          </div>
          <p className="mt-0.5 font-mono text-xs text-muted">
            Step {stepNumber} of {STAGE_ORDER.length}
            {firstEventTime && (status === "running" || status === "pending") && (
              <>
                {" "}· <LiveDuration startIso={firstEventTime} />
              </>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowDetails((v) => !v)}
            className="rounded-lg border border-line bg-base px-2.5 py-1 text-xs text-muted transition-colors hover:border-canonical/50 hover:text-paper"
          >
            {showDetails ? "Hide stages" : "Show stages"}
          </button>
          <span className={`rounded-lg px-2.5 py-1 text-xs font-semibold uppercase tracking-wider ${STATUS_META[status].className}`}>
            {STATUS_META[status].label}
          </span>
        </div>
      </div>

      {/* Prominent Live Agent Activity Card when running */}
      {status === "running" && currentStage && (
        <div className="relative overflow-hidden rounded-2xl border border-canonical/40 bg-gradient-to-br from-canonical/10 via-surface to-base p-5 shadow-xl">
          <div className="flex items-center justify-between gap-3 pb-3 border-b border-line/60">
            <div className="flex items-center gap-2.5">
              <ScanRing />
              <div>
                <span className="text-[10px] font-mono uppercase tracking-widest text-canonical font-bold">
                  Active Stage: {LABELS[currentStage]}
                </span>
                <p className="text-xs text-muted">{STAGE_SUB_HINTS[currentStage]}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-canonical/15 px-2.5 py-0.5 text-[10px] font-mono text-canonical font-medium border border-canonical/30">
                🤖 Gemini 3.7 Flash
              </span>
              <span className="rounded-full bg-physical/15 px-2.5 py-0.5 text-[10px] font-mono text-physical font-medium border border-physical/30">
                AST Verified
              </span>
            </div>
          </div>

          {/* Real-time ticker of latest activity */}
          <div className="pt-3 flex items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-muted shrink-0 font-mono">Current Action:</span>
              <span className="font-mono text-paper font-semibold truncate animate-pulse">
                {latestEvent ? latestEvent.message : "Processing..."}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setShowLiveFeed((v) => !v)}
              className="text-[11px] text-canonical hover:underline shrink-0"
            >
              {showLiveFeed ? "Collapse activity feed" : "Expand activity feed"}
            </button>
          </div>

          {/* Live Activity Stream Terminal */}
          {showLiveFeed && (
            <div
              ref={feedScrollRef}
              className="mt-3 max-h-36 overflow-y-auto rounded-xl border border-line bg-ink/90 p-3 font-mono text-[11px] text-paper/80 space-y-1 select-all"
            >
              {events.map((e, idx) => (
                <div key={idx} className="flex items-start gap-2 leading-relaxed">
                  <span className="text-muted text-[10px] shrink-0">{formatTime(e.timestamp)}</span>
                  <span className="rounded bg-line/80 px-1 py-0.2 text-[9px] uppercase tracking-wider text-canonical shrink-0">
                    {e.stage}
                  </span>
                  <span className="text-paper/90">{e.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Top Multi-Segment Progress Bar */}
      <div className="flex gap-1">
        {STAGE_ORDER.map((stage, index) => {
          const isDone = index < reachedIndex || (index === reachedIndex && status === "succeeded");
          const isCurrent = index === reachedIndex && status !== "succeeded";
          const isActiveScan = isCurrent && status === "running";
          return (
            <div key={stage} className="relative h-2 flex-1 overflow-hidden rounded-full bg-line">
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

      {/* Stage Item List */}
      {showDetails && (
        <ol className="space-y-1">
          {STAGE_ORDER.map((stage, index) => {
            const isDone = index < reachedIndex || (index === reachedIndex && status === "succeeded");
            const isCurrent = index === reachedIndex && status !== "succeeded";
            const isFailed = isCurrent && status === "failed";
            const isNeedsInput = isCurrent && status === "needs_input";
            const isSpinning = isCurrent && status === "running";
            const isLast = index === STAGE_ORDER.length - 1;
            const stageEvents = eventsByStage.get(stage) ?? [];
            const latestMessage = stageEvents[stageEvents.length - 1]?.message;
            const isOpen = expanded.has(stage) && stageEvents.length > 0;
            const sweeping = justResolved === stage;

            const start = stageEvents[0]?.timestamp;
            const nextStart = eventsByStage.get(STAGE_ORDER[index + 1])?.[0]?.timestamp;
            const staticEnd = nextStart ?? (isLast ? stageEvents[stageEvents.length - 1]?.timestamp : undefined);

            return (
              <li key={stage} className="relative flex gap-3 overflow-hidden rounded-xl p-2 transition-colors hover:bg-surface/40">
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

                <div className="min-w-0 flex-1 pb-2">
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
                    <span className="font-display text-sm font-semibold text-paper">{LABELS[stage]}</span>
                    {!isDone && !isCurrent && <span className="text-xs text-muted">pending</span>}
                    {isCurrent && !isFailed && !isNeedsInput && (
                      <span className="text-xs font-semibold text-canonical animate-pulse">resolving…</span>
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
                    {stageEvents.length > 0 && <ChevronIcon open={isOpen} />}
                  </button>

                  {/* Single-line latest message preview when closed */}
                  {!isOpen && latestMessage && (
                    <p className="mt-0.5 truncate font-mono text-xs text-muted">{latestMessage}</p>
                  )}

                  {/* Expanded Sub-events List */}
                  {isOpen && (
                    <div className="mt-2 space-y-2 rounded-xl border border-line/60 bg-base/60 p-3">
                      {stageEvents.map((event, eventIdx) => (
                        <div key={eventIdx} className="text-xs">
                          <div className="flex items-baseline gap-2">
                            <span className="font-mono text-[10px] text-muted">{formatTime(event.timestamp)}</span>
                            <span className="font-mono text-xs text-paper/90">{event.message}</span>
                          </div>
                          {Object.keys(event.data).length > 0 && <EventDataPreview data={event.data} />}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
