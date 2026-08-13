import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  confirmIndustry,
  createRunFromPath,
  createRunFromUpload,
  downloadUrl,
  listPacks,
  setBindingOverrides,
} from "../lib/api";
import { useRunStream } from "../hooks/useRunStream";
import StageTimeline from "../components/StageTimeline";
import ValidationReportView from "../components/ValidationReportView";
import BindingEditor from "../components/BindingEditor";
import PublishPanel from "../components/PublishPanel";
import WarehouseCredentialsPanel from "../components/WarehouseCredentialsPanel";
import type { RankedMatch } from "../lib/types";

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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const opts = { industry: industry || undefined, useLlm };
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
  // ..." -> "Awaiting confirmation"); only the middle one carries
  // ranked_matches, so grab that one specifically rather than the first
  // classify event (which would leave the confirm UI with no options).
  const classifyEvent = [...events].reverse().find((e) => e.stage === "classify" && e.data.ranked_matches);
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

  async function handleConfirmIndustry(slug: string) {
    setBusy(true);
    try {
      await confirmIndustry(runId, slug);
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
      <StageTimeline events={events} status={status} currentStage={currentStage} />

      {status === "needs_input" && classifyEvent && (
        <IndustryConfirm
          matches={classifyEvent.data.ranked_matches ?? []}
          onConfirm={handleConfirmIndustry}
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
      <p className="rounded border border-amber-800/50 bg-amber-950/20 p-3 text-sm text-amber-300">
        Waiting on the classify stage's ranked matches to arrive…
      </p>
    );
  }

  // Mirrors forge_core.classification.matcher.AUTO_ACCEPT_THRESHOLD - this
  // screen only ever renders when the top match was *below* that, so every
  // option here is a low-confidence guess, not a real detection. The top
  // one being listed first (by confidence) doesn't mean it's a good fit -
  // e.g. a B2B sales-pipeline CSV scores "finance" and "retail-ecommerce"
  // almost identically at ~35%, and neither one's KPI assumptions actually
  // match deal-stage values like "Won"/"Lost". Flag that plainly, and
  // point at generic-analytics (no rigid value-set assumptions to mismatch
  // against) as the safe choice when nothing here is a confident, clear
  // description of the data.
  const allLowConfidence = matches.every((m) => m.confidence < 0.45);
  const hasGenericFallback = matches.some((m) => m.pack_slug === "generic-analytics");
  // When every match is a low-confidence guess, the highest-scoring one is
  // *not* a reliable "top pick" - e.g. a sales-pipeline CSV scores "finance"
  // and "retail-ecommerce" almost identically at ~35%, and both would fail
  // validation on deal-stage value mismatches. Users were consistently
  // clicking the first "Use this" button out of habit/position, landing on
  // finance every time despite the warning text above the list. Promoting
  // generic-analytics to the first, visually distinct slot fixes that -
  // the safe option is now what a reflexive first click lands on.
  const promoteGeneric = allLowConfidence && hasGenericFallback;
  const orderedMatches = promoteGeneric
    ? [
        matches.find((m) => m.pack_slug === "generic-analytics")!,
        ...matches.filter((m) => m.pack_slug !== "generic-analytics"),
      ]
    : matches;

  return (
    <div className="space-y-3 rounded border border-sky-800/50 bg-sky-950/20 p-4">
      <p className="text-sm text-sky-300">
        Industry classification was ambiguous. Pick the best match to continue:
      </p>
      {allLowConfidence && (
        <p className="rounded border border-amber-800/50 bg-amber-950/20 p-2 text-xs text-amber-300">
          Every match below is under 45% confidence — these are rough guesses from column names and
          entity hints, not confident detections.
          {hasGenericFallback &&
            " generic-analytics is listed first and recommended below because it has no industry-specific value assumptions (like transaction status wording) that can silently fail validation. Only pick a specialized pack if its \"Matched on\" signals genuinely describe your data."}
        </p>
      )}
      <ul className="space-y-2">
        {orderedMatches.map((match) => {
          const isSafeFallback = promoteGeneric && match.pack_slug === "generic-analytics";
          return (
            <li
              key={match.pack_slug}
              className={
                "flex items-center justify-between gap-3 rounded p-2 text-sm" +
                (isSafeFallback ? " border border-emerald-700/60 bg-emerald-950/20" : "")
              }
            >
              <div className="min-w-0">
                <div>
                  {match.pack_slug}{" "}
                  <span className="text-slate-500">({Math.round(match.confidence * 100)}% confidence)</span>
                  {isSafeFallback && (
                    <span className="ml-2 rounded bg-emerald-900/50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
                      recommended · safe fallback
                    </span>
                  )}
                </div>
                {match.matched_signals.length > 0 && (
                  <div className="truncate text-xs text-slate-500">
                    Matched on: {match.matched_signals.join(", ")}
                  </div>
                )}
              </div>
              <button
                type="button"
                disabled={submitting}
                onClick={() => onConfirm(match.pack_slug)}
                className={
                  "shrink-0 rounded px-3 py-1 text-xs font-medium disabled:opacity-40" +
                  (isSafeFallback ? " bg-emerald-600 text-white" : " border border-slate-700 text-slate-300")
                }
              >
                Use this
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
