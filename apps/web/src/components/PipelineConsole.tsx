import { Fragment, useEffect, useRef, useState } from "react";
import { STAGE_ORDER, type RunStage, type RunStatus, type StageEvent } from "../lib/types";

/** Stage names from the operator's side of the screen, not the pipeline's. */
const STAGE_LABEL: Record<RunStage, string> = {
  ingest: "Reading your tables",
  profile: "Understanding the data",
  classify: "Matching an industry",
  bind: "Mapping your columns",
  compile_kpis: "Compiling metrics",
  generate: "Writing the plugin",
  package: "Packaging",
  validate: "Checking everything",
  publish: "Publishing",
};

const CHECK_LABEL: Record<string, string> = {
  fact_check: "Every reference is real",
  sql_safety: "SQL is read-only & safe",
  dry_run: "Metrics execute for real",
  plugin_spec: "Plugin structure is valid",
  cli_validate: "Passes claude plugin validate",
  mcp_smoke: "MCP server answers",
  self_critique: "AI self-review",
  schema_model: "Knowledge pack is grounded",
};

function fmtClock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour12: false });
}

function fmtDur(ms: number): string {
  if (ms < 950) return "<1s";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function LiveClock({ from }: { from: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return <span className="tabular-nums">{fmtDur(Math.max(Date.now() - new Date(from).getTime(), 0))}</span>;
}

type DotKind = "pending" | "running" | "done" | "failed" | "input";

function StageDot({ kind }: { kind: DotKind }) {
  if (kind === "running")
    return <span className="mt-1 h-2 w-2 shrink-0 animate-pulse-amber rounded-full bg-amber" />;
  const cls =
    kind === "done"
      ? "bg-jade"
      : kind === "failed"
        ? "bg-clay"
        : kind === "input"
          ? "bg-amber"
          : "border border-hair bg-transparent";
  return <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${cls}`} />;
}

function CheckGlyph({ status }: { status: string }) {
  const map: Record<string, string> = {
    pass: "text-jade",
    warn: "text-amber",
    fail: "text-clay",
    skipped: "text-dim",
  };
  const glyph = status === "pass" ? "✓" : status === "fail" ? "✕" : status === "warn" ? "!" : "○";
  return <span className={`font-mono ${map[status] ?? "text-dim"} animate-stage-check`}>{glyph}</span>;
}

function DataStrip({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([k, v]) =>
      !["report", "questions", "review", "check", "status"].includes(k) &&
      v !== null &&
      v !== undefined &&
      !(Array.isArray(v) && v.length === 0)
  );
  if (entries.length === 0) return null;
  return (
    <dl className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-dim">
      {entries.slice(0, 6).map(([k, v]) => (
        <Fragment key={k}>
          <span>
            <span className="text-dim/70">{k.replace(/_/g, " ")}=</span>
            <span className="text-paper/70">
              {Array.isArray(v) ? v.slice(0, 6).join(",") : String(v)}
            </span>
          </span>
        </Fragment>
      ))}
    </dl>
  );
}

interface Props {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
  runId: string | null;
}

export default function PipelineConsole({ events, status, currentStage, runId }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState<Set<RunStage>>(new Set());
  const reachedIndex = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;

  const byStage = new Map<RunStage, StageEvent[]>();
  for (const e of events) {
    const list = byStage.get(e.stage) ?? [];
    list.push(e);
    byStage.set(e.stage, list);
  }
  const started = events[0]?.timestamp;

  useEffect(() => {
    const el = scroller.current;
    el?.scrollTo?.({ top: el.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  const running = status === "running" || status === "pending";

  return (
    <section className="overflow-hidden rounded-xl border border-hair bg-panel">
      <header className="flex items-center gap-2 border-b border-hair px-4 py-2.5 text-xs">
        <span className="font-mono text-jade">▸</span>
        <span className="font-mono font-medium text-paper">forge build</span>
        {runId && <span className="font-mono text-dim">· {runId}</span>}
        <span className="flex-1" />
        {started && running && (
          <span className="font-mono text-dim">
            <LiveClock from={started} />
          </span>
        )}
        <span
          className={
            "rounded-full px-2 py-0.5 font-mono text-[11px] " +
            (status === "succeeded"
              ? "bg-jade/15 text-jade"
              : status === "failed"
                ? "bg-clay/15 text-clay"
                : status === "needs_input"
                  ? "bg-amber/15 text-amber"
                  : "bg-paper/10 text-dim")
          }
        >
          {status === "needs_input" ? "your move" : status}
        </span>
      </header>

      <div ref={scroller} className="max-h-[26rem] overflow-y-auto px-4 py-3 font-mono text-[13px] leading-relaxed">
        {events.length === 0 && (
          <p className="text-dim">
            waiting for a build
            <span className="ml-1 inline-block h-3.5 w-2 translate-y-0.5 animate-caret bg-dim" />
          </p>
        )}

        {STAGE_ORDER.map((stage, i) => {
          const list = byStage.get(stage) ?? [];
          if (list.length === 0 && events.length > 0 && i > reachedIndex + 1) return null;

          const isDone = i < reachedIndex || (i === reachedIndex && status === "succeeded");
          const isCurrent = i === reachedIndex && status !== "succeeded";
          const kind: DotKind =
            isCurrent && status === "failed"
              ? "failed"
              : isCurrent && status === "needs_input"
                ? "input"
                : isCurrent && running
                  ? "running"
                  : isDone
                    ? "done"
                    : "pending";
          const open = list.length > 0 && !collapsed.has(stage);
          const start = list[0]?.timestamp;
          const end = byStage.get(STAGE_ORDER[i + 1])?.[0]?.timestamp ?? list[list.length - 1]?.timestamp;
          const checks = list.filter((e) => typeof e.data?.check === "string");

          return (
            <div key={stage} className={list.length ? "animate-line-in" : ""}>
              <button
                type="button"
                disabled={list.length === 0}
                onClick={() =>
                  setCollapsed((prev) => {
                    const next = new Set(prev);
                    if (next.has(stage)) next.delete(stage);
                    else next.add(stage);
                    return next;
                  })
                }
                className="flex w-full items-baseline gap-2 py-0.5 text-left disabled:cursor-default"
              >
                <StageDot kind={kind} />
                <span className={isDone || isCurrent ? "text-paper" : "text-dim"}>{STAGE_LABEL[stage]}</span>
                <span className="flex-1" />
                {start && end && end !== start && !isCurrent && (
                  <span className="text-dim">{fmtDur(new Date(end).getTime() - new Date(start).getTime())}</span>
                )}
                {isCurrent && running && start && <span className="text-amber"><LiveClock from={start} /></span>}
              </button>

              {open && (
                <div className="mb-1 ml-[9px] border-l border-hair/70 pl-3">
                  {list
                    .filter((e) => typeof e.data?.check !== "string")
                    .map((e, idx) => (
                      <div key={idx} className="animate-line-in py-px">
                        <span className="text-dim">{fmtClock(e.timestamp)}  </span>
                        <span className="text-paper/85">{e.message}</span>
                        <DataStrip data={e.data} />
                      </div>
                    ))}
                  {checks.length > 0 && (
                    <ul className="mt-1 grid gap-y-0.5">
                      {checks.map((e, idx) => (
                        <li key={idx} className="flex items-center gap-2 py-px">
                          <CheckGlyph status={String(e.data.status)} />
                          <span className="text-paper/80">
                            {CHECK_LABEL[String(e.data.check)] ?? String(e.data.check)}
                          </span>
                          <span className="flex-1" />
                          <span className="text-[11px] text-dim">{String(e.data.status)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
