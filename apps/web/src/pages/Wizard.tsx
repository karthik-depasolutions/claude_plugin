import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  cancelRun,
  confirmBindings,
  createRunFromPath,
  createRunFromUpload,
  downloadUrl,
  getRunLogs,
  listPacks,
  setBindingOverrides,
  submitReview,
} from "../lib/api";
import { useRunStream } from "../hooks/useRunStream";
import StageTimeline from "../components/StageTimeline";
import ValidationReportView from "../components/ValidationReportView";
import BindingEditor from "../components/BindingEditor";
import BindingConfirmationPanel from "../components/BindingConfirmationPanel";
import DataReviewPanel from "../components/DataReviewPanel";
import DataUnderstandingPanel from "../components/DataUnderstandingPanel";
import BusinessContextPanel from "../components/BusinessContextPanel";
import PublishPanel from "../components/PublishPanel";
import WarehouseCredentialsPanel from "../components/WarehouseCredentialsPanel";
import DataSourceConnector from "../components/DataSourceConnector";
import RunsDashboard from "../components/RunsDashboard";
import TokenUsagePanel from "../components/TokenUsagePanel";
import type { BindingQuestion, IndustryGuess, RankedMatch, TokenUsage } from "../lib/types";

export default function Wizard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runIdFromUrl = searchParams.get("run_id");
  const isNewFromUrl = searchParams.get("new") === "true";

  const [streamKey, setStreamKey] = useState(0);

  function handleSelectRun(id: string) {
    setSearchParams({ run_id: id });
  }

  function handleStartNew() {
    setSearchParams({ new: "true" });
  }

  function handleBackToDashboard() {
    setSearchParams({});
  }

  function handleRunStarted(id: string) {
    setSearchParams({ run_id: id });
  }

  if (runIdFromUrl) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={handleBackToDashboard}
            className="group flex items-center gap-2 rounded-lg border border-line bg-[#0E121B] px-3 py-1.5 text-xs text-muted transition-colors hover:border-canonical hover:text-paper"
          >
            <span className="transition-transform group-hover:-translate-x-0.5">←</span>
            <span>All Plugins & History</span>
          </button>
          <button
            type="button"
            onClick={handleStartNew}
            className="rounded-lg border border-line bg-ink px-3 py-1.5 text-xs text-muted transition-colors hover:text-paper"
          >
            + New Plugin
          </button>
        </div>

        <RunProgress
          runId={runIdFromUrl}
          streamKey={streamKey}
          onResumed={() => setStreamKey((k) => k + 1)}
          onBack={handleBackToDashboard}
        />
      </div>
    );
  }

  if (isNewFromUrl) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={handleBackToDashboard}
            className="group flex items-center gap-2 rounded-lg border border-line bg-[#0E121B] px-3 py-1.5 text-xs text-muted transition-colors hover:border-canonical hover:text-paper"
          >
            <span className="transition-transform group-hover:-translate-x-0.5">←</span>
            <span>Back to Dashboard</span>
          </button>
        </div>

        <ConnectStep onStarted={handleRunStarted} />
      </div>
    );
  }

  return (
    <RunsDashboard
      onSelectRun={handleSelectRun}
      onNewRun={handleStartNew}
    />
  );
}

function ConnectStep({ onStarted }: { onStarted: (runId: string) => void }) {
  const { data: packs } = useQuery({ queryKey: ["packs"], queryFn: listPacks });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(payload: {
    mode: "upload" | "path" | "database";
    files?: File[];
    sourcePath?: string;
    industry?: string;
    useLlm: boolean;
    useAgent: boolean;
    label?: string;
  }) {
    setError(null);
    setSubmitting(true);
    try {
      const opts = {
        industry: payload.industry,
        useLlm: payload.useLlm,
        useAgent: payload.useAgent,
        label: payload.label,
      };
      const run =
        payload.mode === "upload" && payload.files && payload.files.length > 0
          ? await createRunFromUpload(payload.files, opts)
          : await createRunFromPath(payload.sourcePath || "", opts);
      onStarted(run.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <DataSourceConnector
      packs={packs}
      submitting={submitting}
      error={error}
      onSubmit={handleSubmit}
    />
  );
}

function RunProgress({
  runId,
  streamKey,
  onResumed,
  onBack,
}: {
  runId: string;
  streamKey: number;
  onResumed: () => void;
  onBack: () => void;
}) {
  const { events, status, currentStage, label, refetch } = useRunStream(runId, streamKey);
  const [busy, setBusy] = useState(false);

  // The classify stage logs events
  const classifyEvent = [...events].reverse().find((e) => e.stage === "classify" && e.data.ranked_matches);
  // Which pause is the run *currently* sitting on.
  //
  // A resumed run replays from ingest and appends to the same event list, so
  // the "Awaiting customer input" event from the first pass is still there
  // after its questions have been answered. Searching the whole history for
  // it made `needsAnswers` permanently true, which both re-rendered the
  // answered review panel and suppressed the binding gate that was actually
  // blocking the run — the user answered 13 questions and was handed the
  // same 13 back.
  //
  // Both pause sites log immediately before returning, so the later of the
  // two in the list is the live one; anything earlier is settled history.
  const lastPauseIndex = events.reduce(
    (last, e, i) =>
      (e.stage === "classify" && (e.data.needs_industry || e.data.needs_answers)) ||
      (e.stage === "bind" && e.data.questions)
        ? i
        : last,
    -1
  );
  const livePause = lastPauseIndex >= 0 ? events[lastPauseIndex] : undefined;
  const pauseEvent = livePause?.stage === "classify" ? livePause : undefined;
  const bindPauseEvent = livePause?.stage === "bind" ? livePause : undefined;
  const reviewEvent = [...events].reverse().find((e) => e.stage === "profile" && e.data.review);
  const bindEvent = [...events].reverse().find((e) => e.stage === "bind");
  const validateEvent = [...events].reverse().find((e) => e.stage === "validate");
  const packageEvent = [...events].reverse().find((e) => e.stage === "package" && e.data.plugin_dir);

  // data_understanding is emitted from the profile stage by the agent
  const understandingEvent = [...events].reverse().find(
    (e) => e.stage === "profile" && e.data.data_understanding
  );

  // The Context Discovery Agent's read of the business behind the data.
  const businessContext = [...events].reverse().find(
    (e) => e.stage === "profile" && e.data.business_context
  )?.data.business_context;

  // Cumulative LLM cost, emitted as the last event of a completed run.
  const tokenUsage = [...events].reverse().find((e) => e.data.token_usage)?.data
    .token_usage as TokenUsage | undefined;

  const unresolvedRoles: string[] = bindEvent?.data.unresolved_roles ?? [];
  // From the live pause, not from any historical bind event — a resumed run
  // whose bindings were already confirmed still has the old questions event.
  const bindingQuestions: BindingQuestion[] = bindPauseEvent?.data.questions ?? [];

  const pluginDirPath: string | undefined = packageEvent?.data.plugin_dir;
  const pluginName = pluginDirPath?.split(/[\\\/]/).filter(Boolean).pop();

  const needsAnswers = pauseEvent?.data.needs_answers ?? false;
  const needsIndustry = pauseEvent?.data.needs_industry ?? false;

  // Binding gate: paused with binding questions to answer. Keyed off the
  // live pause rather than "are there any binding questions in history",
  // for the same reason as above.
  const needsBindingConfirmation =
    status === "needs_input" && bindPauseEvent !== undefined && bindingQuestions.length > 0;

  // Unresolved roles (after binding gate, for manual override)
  const canEditBindings =
    unresolvedRoles.length > 0 &&
    (status === "succeeded" || status === "failed" || status === "needs_input") &&
    !needsBindingConfirmation;

  async function handleReviewSubmit(answers: Record<string, string>, industry?: string) {
    setBusy(true);
    try {
      await submitReview(runId, { industry, answers });
      refetch();
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  async function handleBindingConfirmation(confirmations: Record<string, string>) {
    setBusy(true);
    try {
      await confirmBindings(runId, confirmations);
      refetch();
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  async function handleBindingOverrides(overrides: Record<string, string>) {
    setBusy(true);
    try {
      await setBindingOverrides(runId, overrides);
      refetch();
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  async function handleCancelRun() {
    if (!window.confirm("Are you sure you want to stop and cancel this plugin build?")) return;
    setBusy(true);
    try {
      await cancelRun(runId);
      refetch();
      onResumed();
    } catch (err) {
      alert("Failed to stop run: " + err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="animate-reveal-up rounded-xl border border-line bg-[#0E121B] p-5 shadow-lg">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
          <div className="flex items-center gap-2">
            <span className="font-display text-sm font-semibold text-paper">
              {label || `Plugin #${runId.slice(0, 8)}`}
            </span>
            <span className="rounded bg-ink px-2 py-0.5 font-mono text-[10px] text-muted">
              ID: {runId}
            </span>
          </div>

          <div className="flex items-center gap-2.5">
            {(status === "running" || status === "needs_input") && (
              <button
                type="button"
                onClick={handleCancelRun}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg border border-danger/40 bg-danger/10 px-2.5 py-1 text-xs font-semibold text-danger hover:bg-danger/20 hover:border-danger/60 transition-colors cursor-pointer disabled:opacity-50"
                title="Stop execution and cancel run"
              >
                <span>⏹</span>
                <span>Stop Run</span>
              </button>
            )}

            {status === "needs_input" && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/20 px-2.5 py-0.5 text-xs font-semibold text-amber-300">
                <span className="h-2 w-2 animate-ping rounded-full bg-amber-400"></span>
                Action Required — Awaiting Input
              </span>
            )}
            {status === "running" && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/40 bg-cyan-500/20 px-2.5 py-0.5 text-xs font-semibold text-cyan-300">
                <span className="h-2 w-2 animate-ping rounded-full bg-cyan-400"></span>
                Executing Pipeline
              </span>
            )}
            {status === "succeeded" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/20 px-2.5 py-0.5 text-xs font-semibold text-emerald-300">
                ✓ Plugin Ready & Validated
              </span>
            )}
            {status === "failed" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-danger/40 bg-danger/20 px-2.5 py-0.5 text-xs font-semibold text-danger">
                ✕ Build Stopped / Failed
              </span>
            )}
          </div>
        </div>

        <StageTimeline events={events} status={status} currentStage={currentStage} />
      </div>

      {/* What the Context Discovery Agent worked out about the business */}
      {businessContext && <BusinessContextPanel context={businessContext} />}

      {/* data_understanding — show once PROFILE completes */}
      {understandingEvent && (
        <DataUnderstandingPanel understanding={understandingEvent.data.data_understanding} />
      )}

      {/* Binding gate — takes priority over the review panel */}
      {needsBindingConfirmation && (
        <BindingConfirmationPanel
          questions={bindingQuestions}
          onSubmit={handleBindingConfirmation}
          submitting={busy}
        />
      )}

      {/* Data-quality / industry review pause */}
      {status === "needs_input" && pauseEvent && !needsBindingConfirmation && (
        <DataReviewPanel
          review={reviewEvent?.data.review ?? { generated_at: "", findings: [], questions: [], skipped_tables: [] }}
          needsAnswers={needsAnswers}
          needsIndustry={needsIndustry}
          matches={(classifyEvent?.data.ranked_matches ?? []) as RankedMatch[]}
          industryGuess={classifyEvent?.data.suggested_industry as IndustryGuess | undefined}
          onSubmit={handleReviewSubmit}
          submitting={busy}
        />
      )}

      {canEditBindings && (
        <BindingEditor unresolvedRoles={unresolvedRoles} onSubmit={handleBindingOverrides} submitting={busy} />
      )}

      {status === "failed" && (
        <div className="animate-reveal-up rounded-xl border border-danger/30 bg-danger/10 p-4 space-y-3">
          <div className="flex items-center gap-2 text-danger font-semibold text-sm">
            <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>Run execution encountered a failure</span>
          </div>
          <p className="text-xs text-paper/80">
            Review the timeline or view the execution logs below to see details.
          </p>
          <RunLogViewer runId={runId} />
        </div>
      )}

      {validateEvent?.data.report && <ValidationReportView report={validateEvent.data.report} />}

      {tokenUsage && <TokenUsagePanel usage={tokenUsage} />}

      {status === "succeeded" && (
        <div className="animate-reveal-up space-y-4">
          <WarehouseCredentialsPanel runId={runId} />
          <div className="flex flex-wrap items-center gap-3">
            <a
              href={downloadUrl(runId)}
              download
              className="inline-flex items-center gap-2 rounded-lg bg-canonical px-5 py-2.5 text-sm font-bold text-ink shadow-lg shadow-canonical/20 transition-all hover:scale-105 hover:brightness-105"
            >
              <span>Download Plugin (.zip)</span>
              <span>↓</span>
            </a>
            <button
              type="button"
              onClick={onBack}
              className="rounded-lg border border-line bg-ink px-4 py-2.5 text-sm font-medium text-paper hover:bg-line/40 transition-colors"
            >
              Return to My Plugins
            </button>
          </div>
          <PublishPanel runId={runId} defaultRepoName={pluginName} />
        </div>
      )}
    </div>
  );
}

function RunLogViewer({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const { data: logs, isLoading } = useQuery({
    queryKey: ["run-logs", runId],
    queryFn: () => getRunLogs(runId),
    enabled: open,
  });

  return (
    <div className="rounded-lg border border-line bg-base/80 overflow-hidden text-xs">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-muted hover:text-paper hover:bg-white/5 transition-colors font-mono"
      >
        <span>{open ? "▼ Hide Execution Logs" : "▶ View Detailed Execution Logs"}</span>
        <span className="text-[10px] text-muted">pipeline.log</span>
      </button>

      {open && (
        <div className="p-3 border-t border-line/60 bg-ink">
          {isLoading ? (
            <p className="text-muted font-mono">Loading execution logs…</p>
          ) : logs?.log_text ? (
            <pre className="max-h-64 overflow-y-auto font-mono text-[11px] text-paper/80 whitespace-pre-wrap select-all">
              {logs.log_text}
            </pre>
          ) : logs?.error ? (
            <div className="font-mono text-danger text-[11px]">
              <p className="font-bold mb-1">Error message:</p>
              <p>{logs.error}</p>
            </div>
          ) : (
            <p className="text-muted font-mono">No log entries recorded yet.</p>
          )}
        </div>
      )}
    </div>
  );
}
