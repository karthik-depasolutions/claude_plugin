import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  confirmIndustry,
  createRunFromPath,
  createRunFromUpload,
  getRun,
  listPacks,
  setBindingOverrides,
  submitDataAnswers,
} from "../lib/api";
import { useRunStream } from "../hooks/useRunStream";
import PipelineConsole from "../components/PipelineConsole";
import ClarifyPanel from "../components/ClarifyPanel";
import PluginResult from "../components/PluginResult";
import ValidationReportView from "../components/ValidationReportView";
import BindingEditor from "../components/BindingEditor";
import type { RankedMatch } from "../lib/types";

export default function Wizard() {
  const [runId, setRunId] = useState<string | null>(null);
  const [streamKey, setStreamKey] = useState(0);
  const [sourceLabel, setSourceLabel] = useState("");

  if (!runId) {
    return (
      <ConnectForm
        onStarted={(id, label) => {
          setRunId(id);
          setSourceLabel(label);
        }}
      />
    );
  }
  return (
    <RunView
      runId={runId}
      streamKey={streamKey}
      sourceLabel={sourceLabel}
      onResumed={() => setStreamKey((k) => k + 1)}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Before a run: the thesis line + the connect form, on the machine ground.   */
/* -------------------------------------------------------------------------- */
function ConnectForm({ onStarted }: { onStarted: (runId: string, label: string) => void }) {
  const { data: packs } = useQuery({ queryKey: ["packs"], queryFn: listPacks });
  const [mode, setMode] = useState<"upload" | "path">("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [path, setPath] = useState("");
  const [industry, setIndustry] = useState("");
  const [label, setLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = mode === "upload" ? files.length > 0 : path.trim().length > 0;

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const opts = { industry: industry || undefined, label: label.trim() || undefined };
      const run =
        mode === "upload" && files.length > 0
          ? await createRunFromUpload(files, opts)
          : await createRunFromPath(path, opts);
      const shown = label.trim() || files.map((f) => f.name).join(", ") || path;
      onStarted(run.run_id, shown);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:items-center">
      <div>
        <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-jade">Plugin foundry</p>
        <h1 className="mt-3 font-display text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl">
          Point Forge at a dataset.
        </h1>
        <p className="mt-4 max-w-md text-sm leading-relaxed text-dim">
          It profiles your tables, asks a few questions about what the data means, then compiles a
          Claude Code plugin scoped to it &mdash; verified metrics, a query cookbook, and an eight-check
          safety gate before anything ships.
        </p>
      </div>

      <div className="rounded-xl border border-hair bg-panel p-5 sm:p-6">
        <div className="flex gap-1 rounded-lg bg-void p-1 text-xs">
          {(["upload", "path"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={
                "flex-1 rounded-md px-3 py-1.5 transition-colors " +
                (mode === m ? "bg-panel text-paper" : "text-dim hover:text-paper")
              }
            >
              {m === "upload" ? "Upload files" : "Server path"}
            </button>
          ))}
        </div>

        <div className="mt-4 space-y-4">
          {mode === "upload" ? (
            <div>
              <input
                type="file"
                multiple
                accept=".csv,.tsv,.json,.ndjson,.parquet,.xlsx,.xls,.sqlite,.db,.zip"
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                className="block w-full text-xs text-dim file:mr-3 file:rounded-md file:border-0 file:bg-jade file:px-3 file:py-1.5 file:text-doc-ink file:transition-opacity hover:file:opacity-90"
              />
              <p className="mt-1.5 text-[11px] text-dim">
                One row of tables per file, or a single <code className="text-paper/70">.zip</code>.
              </p>
              {files.length > 0 && (
                <ul className="mt-2 space-y-0.5 font-mono text-[11px] text-dim">
                  {files.map((f) => (
                    <li key={f.name}>{f.name}</li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/data/orders.csv, a folder of tables, or postgresql://…"
              className="w-full rounded-md border border-hair bg-void px-3 py-2 text-sm placeholder:text-dim focus:border-jade focus:outline-none focus:ring-2 focus:ring-jade/25"
            />
          )}

          <Field label="Project name" hint="Names the plugin and its repo. Optional.">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Sparda Music Academy"
              className="w-full rounded-md border border-hair bg-void px-3 py-2 text-sm placeholder:text-dim focus:border-jade focus:outline-none focus:ring-2 focus:ring-jade/25"
            />
          </Field>

          <Field label="Industry" hint="Skips auto-detection.">
            <select
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              className="w-full rounded-md border border-hair bg-void px-3 py-2 text-sm focus:border-jade focus:outline-none focus:ring-2 focus:ring-jade/25"
            >
              <option value="">Auto-detect</option>
              {packs?.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          {error && <p className="text-xs text-clay">{error}</p>}

          <button
            type="button"
            disabled={!canSubmit || submitting}
            onClick={submit}
            className="w-full rounded-md bg-amber px-4 py-2.5 text-sm font-medium text-doc-ink transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {submitting ? "Starting…" : "Build plugin"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-paper/80">{label}</span>
      <span className="ml-2 text-[11px] text-dim">{hint}</span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

/* -------------------------------------------------------------------------- */
/* During / after a run: the console is the page.                            */
/* -------------------------------------------------------------------------- */
function RunView({
  runId,
  streamKey,
  sourceLabel,
  onResumed,
}: {
  runId: string;
  streamKey: number;
  sourceLabel: string;
  onResumed: () => void;
}) {
  const { events, status, currentStage, questions } = useRunStream(runId, streamKey);
  const [busy, setBusy] = useState(false);

  // Token usage is finalized only when the pipeline thread exits; the SSE
  // stream doesn't carry it, so pull the run detail once on success.
  const { data: detail } = useQuery({
    queryKey: ["run", runId, "detail"],
    queryFn: () => getRun(runId),
    enabled: status === "succeeded",
  });

  const classifyEvent = [...events].reverse().find((e) => e.stage === "classify" && e.data.ranked_matches);
  const bindEvent = [...events].reverse().find((e) => e.stage === "bind");
  const validateEvent = [...events].reverse().find((e) => e.stage === "validate" && e.data.report);
  const report = validateEvent?.data.report ?? null;
  const unresolvedRoles: string[] = bindEvent?.data.unresolved_roles ?? [];

  const paused = status === "needs_input";
  const askingQuestions = paused && currentStage === "profile" && questions.length > 0;
  const confirmingIndustry = paused && !askingQuestions && !!classifyEvent;
  const canEditBindings =
    unresolvedRoles.length > 0 &&
    (status === "succeeded" || status === "failed" || (paused && !askingQuestions));

  async function guard<T>(fn: () => Promise<T>) {
    setBusy(true);
    try {
      await fn();
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-dim">
        <span className="truncate font-medium text-paper/80">{sourceLabel || "your data"}</span>
        <span className="font-mono">· run {runId}</span>
      </div>

      <PipelineConsole events={events} status={status} currentStage={currentStage} runId={runId} />

      {askingQuestions && (
        <ClarifyPanel
          questions={questions}
          busy={busy}
          onSubmit={(answers) => guard(() => submitDataAnswers(runId, answers))}
        />
      )}

      {confirmingIndustry && (
        <IndustryConfirm
          matches={classifyEvent!.data.ranked_matches ?? []}
          submitting={busy}
          onConfirm={(slug) => guard(() => confirmIndustry(runId, slug))}
        />
      )}

      {canEditBindings && (
        <div className="rounded-xl border border-hair bg-panel p-5">
          <BindingEditor
            unresolvedRoles={unresolvedRoles}
            submitting={busy}
            onSubmit={(o) => guard(() => setBindingOverrides(runId, o))}
          />
        </div>
      )}

      {status === "failed" && (
        <section className="rounded-xl bg-doc p-6 text-doc-ink shadow-lg shadow-black/20">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-clay">Build stopped</p>
          <h2 className="mt-1.5 font-display text-xl font-semibold">
            It stopped at &ldquo;{currentStage ?? "an early stage"}&rdquo;.
          </h2>
          <p className="mt-2 text-sm text-doc-ink/70">
            {bindEvent && status === "failed"
              ? "See the console above for the stage that raised the error."
              : "The console above has the full trace."}
          </p>
        </section>
      )}

      {status === "succeeded" && (
        <PluginResult
          runId={runId}
          events={events}
          report={report}
          tokenUsage={detail?.token_usage ?? null}
        />
      )}

      {report && status !== "succeeded" && <ValidationReportView report={report} />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Low-confidence industry pick (ported, rethemed).                          */
/* -------------------------------------------------------------------------- */
function IndustryConfirm({
  matches,
  onConfirm,
  submitting,
}: {
  matches: RankedMatch[];
  onConfirm: (slug: string) => void;
  submitting: boolean;
}) {
  if (matches.length === 0) {
    return (
      <p className="rounded-xl border border-hair bg-panel p-4 text-xs text-amber">
        Waiting on the match scores…
      </p>
    );
  }

  const allLow = matches.every((m) => m.confidence < 0.45);
  const hasGeneric = matches.some((m) => m.pack_slug === "generic-analytics");
  const promoteGeneric = allLow && hasGeneric;
  const ordered = promoteGeneric
    ? [
        matches.find((m) => m.pack_slug === "generic-analytics")!,
        ...matches.filter((m) => m.pack_slug !== "generic-analytics"),
      ]
    : matches;

  return (
    <section className="rounded-xl bg-doc p-6 text-doc-ink shadow-lg shadow-black/20">
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-amber">Your move</p>
      <h2 className="mt-1.5 font-display text-xl font-semibold tracking-tight">Which industry fits?</h2>
      <p className="mt-2 text-sm text-doc-ink/70">
        No single pack matched confidently. Pick the one whose signals actually describe your data.
        {promoteGeneric && " generic-analytics makes no value assumptions and is the safe choice."}
      </p>

      <ul className="mt-5 space-y-2">
        {ordered.map((m) => {
          const safe = promoteGeneric && m.pack_slug === "generic-analytics";
          return (
            <li
              key={m.pack_slug}
              className={
                "flex items-center justify-between gap-3 rounded-lg border p-3 " +
                (safe ? "border-jade/50 bg-jade/5" : "border-doc-hair")
              }
            >
              <div className="min-w-0">
                <div className="text-sm font-medium">
                  {m.pack_slug}{" "}
                  <span className="text-doc-ink/45">{Math.round(m.confidence * 100)}%</span>
                  {safe && (
                    <span className="ml-2 rounded bg-jade/15 px-1.5 py-0.5 text-[10px] font-medium text-jade">
                      recommended
                    </span>
                  )}
                </div>
                {m.matched_signals.length > 0 && (
                  <div className="truncate text-xs text-doc-ink/50">
                    matched on {m.matched_signals.join(", ")}
                  </div>
                )}
              </div>
              <button
                type="button"
                disabled={submitting}
                onClick={() => onConfirm(m.pack_slug)}
                className={
                  "shrink-0 rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-40 " +
                  (safe
                    ? "bg-jade text-doc-ink"
                    : "border border-doc-hair text-doc-ink hover:bg-doc-ink/5")
                }
              >
                Use this
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
