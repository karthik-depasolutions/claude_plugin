import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
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
import DataReviewPanel from "../components/DataReviewPanel";
import PublishPanel from "../components/PublishPanel";
import WarehouseCredentialsPanel from "../components/WarehouseCredentialsPanel";
import type { IndustryGuess, RankedMatch } from "../lib/types";

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
  const [label, setLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const opts = { industry: industry || undefined, useLlm, label: label.trim() || undefined };
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
            className="block w-full text-sm text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-sky-600 file:px-3 file:py-1.5 file:text-white"
          />
          <p className="text-xs text-slate-500">
            Select multiple CSV/Excel/JSON/Parquet files for a multi-table source, or a single{" "}
            <code>.zip</code> containing them.
          </p>
          {files.length > 0 && (
            <ul className="text-xs text-slate-400">
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
          className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
        />
      )}

      <label className="block text-sm text-slate-300">
        Project / business name (optional)
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Sparda Music Academy"
          className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2"
        />
        <span className="mt-1 block text-xs text-slate-500">
          Names the plugin and (for warehouse-backed uploads) the database schema. Defaults to a
          generic name based on the detected industry if left blank.
        </span>
      </label>

      <label className="block text-sm text-slate-300">
        Industry (optional - skips auto-classification)
        <select
          value={industry}
          onChange={(e) => setIndustry(e.target.value)}
          className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2"
        >
          <option value="">Auto-detect</option>
          {packs?.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
        Use Gemini for semantic profiling, prose generation, and self-critique
      </label>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <button
        type="button"
        disabled={!canSubmit || submitting}
        onClick={submit}
        className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
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
      className={`rounded px-3 py-1.5 ${active ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"}`}
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
  const unresolvedRoles: string[] = bindEvent?.data.unresolved_roles ?? [];
  // plugin_dir is a server filesystem path (e.g. "generated\runs\<id>\output\
  // <pack>-mis-plugin") - only the last segment (the plugin's own directory
  // name, which is also its manifest `name`) is meaningful client-side.
  const pluginDirPath: string | undefined = packageEvent?.data.plugin_dir;
  const pluginName = pluginDirPath?.split(/[\\/]/).filter(Boolean).pop();
  const canEditBindings =
    unresolvedRoles.length > 0 && (status === "succeeded" || status === "failed" || status === "needs_input");
  const needsAnswers = pauseEvent?.data.needs_answers ?? false;
  const needsIndustry = pauseEvent?.data.needs_industry ?? false;

  async function handleReviewSubmit(answers: Record<string, string>, industry?: string) {
    setBusy(true);
    try {
      await submitReview(runId, { industry, answers });
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
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <div className="mb-3 flex items-center justify-between border-b border-slate-800/80 pb-3">
          <span className="text-xs text-slate-500">
            Run <span className="font-mono text-slate-400">{runId}</span>
          </span>
        </div>
        <StageTimeline events={events} status={status} currentStage={currentStage} />
      </div>

      {status === "needs_input" && pauseEvent && (
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
        <p className="rounded border border-red-800/50 bg-red-950/20 p-3 text-sm text-red-300">
          Run failed. Check the timeline above for the stage that raised the error.
        </p>
      )}

      {validateEvent?.data.report && <ValidationReportView report={validateEvent.data.report} />}

      {status === "succeeded" && (
        <div className="space-y-4">
          <WarehouseCredentialsPanel runId={runId} />
          <a
            href={downloadUrl(runId)}
            className="inline-block rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white"
          >
            Download plugin (.zip)
          </a>
          <PublishPanel runId={runId} defaultRepoName={pluginName} />
        </div>
      )}
    </div>
  );
}
