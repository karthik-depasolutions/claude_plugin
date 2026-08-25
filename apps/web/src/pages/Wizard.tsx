import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  confirmBindings,
  createRunFromPath,
  createRunFromUpload,
  downloadUrl,
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
import PublishPanel from "../components/PublishPanel";
import WarehouseCredentialsPanel from "../components/WarehouseCredentialsPanel";
import type { BindingQuestion, IndustryGuess, RankedMatch } from "../lib/types";

export default function Wizard() {
  const [runId, setRunId] = useState<string | null>(null);
  const [streamKey, setStreamKey] = useState(0);

  if (!runId) {
    return <ConnectStep onStarted={setRunId} />;
  }
  return <RunProgress runId={runId} streamKey={streamKey} onResumed={() => setStreamKey((k) => k + 1)} />;
}

function ConnectStep({ onStarted }: { onStarted: (runId: string) => void }) {
  const { data: packs } = useQuery({ queryKey: ["packs"], queryFn: listPacks });
  const [mode, setMode] = useState<"upload" | "path">("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [path, setPath] = useState("");
  const [industry, setIndustry] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const [useAgent, setUseAgent] = useState(true);
  const [label, setLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const opts = {
        industry: industry || undefined,
        useLlm,
        useAgent: useLlm ? useAgent : false,
        label: label.trim() || undefined,
      };
      const run =
        mode === "upload" && files.length > 0
          ? await createRunFromUpload(files, opts)
          : await createRunFromPath(path, opts);
      onStarted(run.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = mode === "upload" ? files.length > 0 : path.trim().length > 0;

  return (
    <div className="space-y-6">
      <div className="flex gap-2 text-sm">
        <TabButton active={mode === "upload"} onClick={() => setMode("upload")}>
          Upload a file
        </TabButton>
        <TabButton active={mode === "path"} onClick={() => setMode("path")}>
          Server path
        </TabButton>
      </div>

      {mode === "upload" ? (
        <div className="space-y-2">
          <input
            type="file"
            multiple
            accept=".csv,.tsv,.json,.ndjson,.parquet,.xlsx,.xls,.sqlite,.db,.zip"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            className="block w-full text-sm text-paper/80 file:mr-3 file:rounded file:border-0 file:bg-canonical file:px-3 file:py-1.5 file:font-medium file:text-ink file:transition-colors hover:file:bg-canonical/85"
          />
          <p className="text-xs text-muted">
            Select multiple CSV/Excel/JSON/Parquet files for a multi-table source, or a single{" "}
            <code className="rounded bg-line px-1 py-0.5 font-mono text-[11px]">.zip</code> containing them.
          </p>
          {files.length > 0 && (
            <ul className="font-mono text-xs text-paper/70">
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
          placeholder="/path/to/dataset.csv or a directory of tables"
          className="w-full rounded border border-line bg-[#0E121B] px-3 py-2 text-sm text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
        />
      )}

      <label className="block text-sm text-paper/80">
        Project / business name (optional)
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Sparda Music Academy"
          className="mt-1 w-full rounded border border-line bg-[#0E121B] px-3 py-2 text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
        />
        <span className="mt-1 block text-xs text-muted">
          Names the plugin and (for warehouse-backed uploads) the database schema. Defaults to a
          generic name based on the detected industry if left blank.
        </span>
      </label>

      <label className="block text-sm text-paper/80">
        Industry (optional - skips auto-classification)
        <select
          value={industry}
          onChange={(e) => setIndustry(e.target.value)}
          className="mt-1 w-full rounded border border-line bg-[#0E121B] px-3 py-2 text-paper focus:border-canonical focus:outline-none"
        >
          <option value="">Auto-detect</option>
          {packs?.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm text-paper/80">
          <input
            type="checkbox"
            checked={useLlm}
            onChange={(e) => {
              setUseLlm(e.target.checked);
              if (!e.target.checked) setUseAgent(false);
            }}
            className="accent-canonical"
          />
          Use Gemini for semantic profiling, prose generation, and self-critique
        </label>
        {useLlm && (
          <label className="flex items-center gap-2 pl-6 text-sm text-paper/70">
            <input
              type="checkbox"
              checked={useAgent}
              onChange={(e) => setUseAgent(e.target.checked)}
              className="accent-canonical"
            />
            Use tool-using agent for deeper column understanding (recommended)
          </label>
        )}
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      <button
        type="button"
        disabled={!canSubmit || submitting}
        onClick={submit}
        className="rounded bg-physical px-4 py-2 text-sm font-semibold text-ink transition-transform hover:scale-[1.02] disabled:scale-100 disabled:opacity-40"
      >
        {submitting ? "Starting…" : "Generate plugin"}
      </button>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-3 py-1.5 transition-colors ${
        active ? "bg-canonical/15 text-canonical" : "text-muted hover:text-paper"
      }`}
    >
      {children}
    </button>
  );
}

function RunProgress({
  runId,
  streamKey,
  onResumed,
}: {
  runId: string;
  streamKey: number;
  onResumed: () => void;
}) {
  const { events, status, currentStage } = useRunStream(runId, streamKey);
  const [busy, setBusy] = useState(false);

  // The classify stage logs 3 events ("Classifying industry" -> "Top match:
  // ..." -> "Awaiting customer input"); ranked_matches rides the middle one,
  // and the merged pause's needs_industry/needs_answers flags ride the last.
  const classifyEvent = [...events].reverse().find((e) => e.stage === "classify" && e.data.ranked_matches);
  const pauseEvent = [...events].reverse().find(
    (e) => e.stage === "classify" && (e.data.needs_industry || e.data.needs_answers)
  );
  const reviewEvent = [...events].reverse().find((e) => e.stage === "profile" && e.data.review);
  const bindEvent = [...events].reverse().find((e) => e.stage === "bind");
  const validateEvent = [...events].reverse().find((e) => e.stage === "validate");
  const packageEvent = [...events].reverse().find((e) => e.stage === "package" && e.data.plugin_dir);

  // data_understanding is emitted from the profile stage by the agent
  const understandingEvent = [...events].reverse().find(
    (e) => e.stage === "profile" && e.data.data_understanding
  );

  const unresolvedRoles: string[] = bindEvent?.data.unresolved_roles ?? [];
  const bindingQuestions: BindingQuestion[] = bindEvent?.data.questions ?? [];

  // plugin_dir is a server filesystem path (e.g. "generated\runs\<id>\output\
  // <pack>-mis-plugin") - only the last segment (the plugin's own directory
  // name, which is also its manifest `name`) is meaningful client-side.
  const pluginDirPath: string | undefined = packageEvent?.data.plugin_dir;
  const pluginName = pluginDirPath?.split(/[\\\/]/).filter(Boolean).pop();

  const needsAnswers = pauseEvent?.data.needs_answers ?? false;
  const needsIndustry = pauseEvent?.data.needs_industry ?? false;

  // Binding gate: paused with binding questions to answer
  const needsBindingConfirmation =
    status === "needs_input" &&
    bindingQuestions.length > 0 &&
    !needsAnswers &&
    !needsIndustry;

  // Unresolved roles (after binding gate, for manual override)
  const canEditBindings =
    unresolvedRoles.length > 0 &&
    (status === "succeeded" || status === "failed" || status === "needs_input") &&
    !needsBindingConfirmation;

  async function handleReviewSubmit(answers: Record<string, string>, industry?: string) {
    setBusy(true);
    try {
      await submitReview(runId, { industry, answers });
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  async function handleBindingConfirmation(confirmations: Record<string, string>) {
    setBusy(true);
    try {
      await confirmBindings(runId, confirmations);
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  async function handleBindingOverrides(overrides: Record<string, string>) {
    setBusy(true);
    try {
      await setBindingOverrides(runId, overrides);
      onResumed();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="animate-reveal-up rounded-lg border border-line bg-[#0E121B] p-4">
        <div className="mb-3 flex items-center justify-between border-b border-line pb-3">
          <span className="text-xs text-muted">
            Run <span className="font-mono text-paper/70">{runId}</span>
          </span>
        </div>
        <StageTimeline events={events} status={status} currentStage={currentStage} />
      </div>

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
        <p className="animate-reveal-up rounded border border-danger/30 bg-danger/10 p-3 text-sm text-danger">
          Run failed. Check the timeline above for the stage that raised the error.
        </p>
      )}

      {validateEvent?.data.report && <ValidationReportView report={validateEvent.data.report} />}

      {status === "succeeded" && (
        <div className="animate-reveal-up space-y-4">
          <WarehouseCredentialsPanel runId={runId} />
          <a
            href={downloadUrl(runId)}
            className="inline-block rounded bg-physical px-4 py-2 text-sm font-semibold text-ink transition-transform hover:scale-[1.02]"
          >
            Download plugin (.zip)
          </a>
          <PublishPanel runId={runId} defaultRepoName={pluginName} />
        </div>
      )}
    </div>
  );
}
