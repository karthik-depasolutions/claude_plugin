import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  confirmIndustry,
  createRunFromPath,
  createRunFromUpload,
  getHealth,
  getRun,
  listPacks,
  setBindingOverrides,
  submitDataAnswers,
} from "../lib/api";
import { useRunStream } from "../hooks/useRunStream";
import type { RankedMatch } from "../lib/types";
import { cx } from "../components/ui/cx";
import PipelineConsole from "../components/PipelineConsole";
import ClarifyPanel from "../components/ClarifyPanel";
import PluginResult from "../components/PluginResult";
import ValidationReportView from "../components/ValidationReportView";
import BindingEditor from "../components/BindingEditor";
import FileDropzone from "../components/wizard/FileDropzone";
import Button from "../components/ui/Button";
import Field from "../components/ui/Field";
import Input from "../components/ui/Input";
import Panel from "../components/ui/Panel";
import PageHeader from "../components/ui/PageHeader";
import Select from "../components/ui/Select";
import SegmentedControl from "../components/ui/SegmentedControl";
import StageRail from "../components/ui/StageRail";
import ErrorState from "../components/ui/ErrorState";

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
/* Before a run: pick a source and start.                                     */
/* -------------------------------------------------------------------------- */
function ConnectForm({ onStarted }: { onStarted: (runId: string, label: string) => void }) {
  const { data: packs, isLoading: packsLoading } = useQuery({ queryKey: ["packs"], queryFn: listPacks });
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const [mode, setMode] = useState<"upload" | "path">("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [path, setPath] = useState("");
  const [dataMode, setDataMode] = useState<"live" | "clone">("live");
  const [agentSchema, setAgentSchema] = useState(false);
  const [publishAgentsToSource, setPublishAgentsToSource] = useState(false);
  const [noLlm, setNoLlm] = useState(false);
  const [industry, setIndustry] = useState("");
  const [label, setLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = mode === "upload" ? files.length > 0 : path.trim().length > 0;
  const isPgUrl = mode === "path" && /^postgres(ql)?(\+[a-z0-9_]+)?:\/\//i.test(path.trim());
  const warehouseOk = health?.client_warehouse ?? false;

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const opts = {
        industry: industry || undefined,
        label: label.trim() || undefined,
        agentSchema: agentSchema || undefined,
        noLlm: noLlm || undefined,
      };
      const run =
        mode === "upload" && files.length > 0
          ? await createRunFromUpload(files, opts)
          : await createRunFromPath(path, {
              ...opts,
              clone: isPgUrl && dataMode === "clone",
              publishAgentsToSource: isPgUrl && dataMode === "live" && publishAgentsToSource,
            });
      const shown = label.trim() || files.map((f) => f.name).join(", ") || path;
      onStarted(run.run_id, shown);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Plugin foundry"
        title="Point Forge at a dataset."
        description="It profiles your tables, asks a few questions about what the data means, then compiles a Claude Code plugin scoped to it — verified metrics, a query cookbook, and an eight-check safety gate before anything ships."
      />

      <Panel className="max-w-2xl">
        <div className="space-y-5">
          <SegmentedControl
            aria-label="Data source"
            value={mode}
            onChange={setMode}
            options={[
              { value: "upload", label: "Upload files" },
              { value: "path", label: "Server path" },
            ]}
          />

          {mode === "upload" ? (
            <FileDropzone files={files} onChange={setFiles} processing={submitting} />
          ) : (
            <Field label="Source" hint="File, folder, SQLite, or a connection string">
              <Input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/data/orders.csv, a folder of tables, or postgresql://…"
              />
            </Field>
          )}

          {isPgUrl && (
            <fieldset className="space-y-2">
              <legend className="flex items-baseline gap-2">
                <span className="text-[13px] font-medium text-text">Database access</span>
                <span className="text-xs text-text-faint">how the plugin reads this database</span>
              </legend>
              {(
                [
                  {
                    value: "live" as const,
                    title: "Query live",
                    body: "Forge reads your database read-only during the build; the plugin connects to it live. Nothing is copied.",
                    disabled: false,
                  },
                  {
                    value: "clone" as const,
                    title: "Clone to Forge",
                    body: warehouseOk
                      ? "A point-in-time copy of this schema is stored on Forge's warehouse; the plugin reads the copy. Up to 25 tables / 2M rows."
                      : "Unavailable — the Forge data warehouse isn't configured on this server.",
                    disabled: !warehouseOk,
                  },
                ]
              ).map((opt) => (
                <label
                  key={opt.value}
                  className={cx(
                    "flex gap-2.5 rounded-[var(--radius-sm)] border border-border p-2.5",
                    opt.disabled
                      ? "opacity-55"
                      : "cursor-pointer has-[:checked]:border-border-strong has-[:checked]:bg-surface-2",
                  )}
                >
                  <input
                    type="radio"
                    name="dataMode"
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
                    checked={dataMode === opt.value}
                    disabled={opt.disabled}
                    onChange={() => setDataMode(opt.value)}
                  />
                  <span className="min-w-0">
                    <span className="text-[13px] font-medium text-text">{opt.title}</span>
                    <span className="mt-0.5 block text-[12px] leading-relaxed text-text-muted">
                      {opt.body}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}

          {isPgUrl && dataMode === "live" && (
            <label
              className={cx(
                "flex gap-2.5 rounded-[var(--radius-sm)] border border-border p-2.5",
                "cursor-pointer has-[:checked]:border-border-strong has-[:checked]:bg-surface-2",
              )}
            >
              <input
                type="checkbox"
                className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
                checked={publishAgentsToSource}
                onChange={(e) => setPublishAgentsToSource(e.target.checked)}
              />
              <span className="min-w-0">
                <span className="text-[13px] font-medium text-text">
                  Publish AGENTS schema into this database
                </span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-text-muted">
                  Also writes an <code>AGENTS.*</code> metadata schema (dbt agents_schema shape) into
                  your source database so other SQL agents can discover it. Needs CREATE rights on the
                  connection; skipped harmlessly if not. The generated plugin stays read-only.
                </span>
              </span>
            </label>
          )}

          <Field label="Project name" hint="Names the plugin and its repo — optional">
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Sparda Music Academy"
            />
          </Field>

          <label
            className={cx(
              "flex gap-2.5 rounded-[var(--radius-sm)] border border-border p-2.5",
              "cursor-pointer has-[:checked]:border-border-strong has-[:checked]:bg-surface-2",
            )}
          >
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
              checked={agentSchema}
              onChange={(e) => setAgentSchema(e.target.checked)}
            />
            <span className="min-w-0">
              <span className="text-[13px] font-medium text-text">Agent Schema mode</span>
              <span className="mt-0.5 block text-[12px] leading-relaxed text-text-muted">
                For a large database (roughly 25+ tables). Reads declared keys to skip slow
                relationship probing, budgets the profiler, and ships a searchable, sharded
                catalog the plugin queries on demand instead of one big file. Leave off for
                small datasets — output is unchanged.
              </span>
            </span>
          </label>

          <label
            className={cx(
              "flex gap-2.5 rounded-[var(--radius-sm)] border border-border p-2.5",
              "cursor-pointer has-[:checked]:border-border-strong has-[:checked]:bg-surface-2",
            )}
          >
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
              checked={noLlm}
              onChange={(e) => setNoLlm(e.target.checked)}
            />
            <span className="min-w-0">
              <span className="text-[13px] font-medium text-text">Fast test run (no&nbsp;LLM)</span>
              <span className="mt-0.5 block text-[12px] leading-relaxed text-text-muted">
                Runs the whole pipeline with a stub model — no API calls, no cost. Skips the
                clarification pause and, with Agent Schema mode, the slow profiling passes: a
                70-table database finishes in a few minutes. The generated plugin has template
                prose and no verified cookbook — this is for checking the pipeline, not shipping.
              </span>
            </span>
          </label>

          <Field label="Industry" hint="Skips auto-detection">
            <Select value={industry} onChange={(e) => setIndustry(e.target.value)} disabled={packsLoading}>
              <option value="">{packsLoading ? "Loading packs…" : "Auto-detect"}</option>
              {packs?.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>

          {error && (
            <p className="text-[13px] text-danger" role="alert">
              {error}
            </p>
          )}

          <Button
            type="button"
            variant="primary"
            disabled={!canSubmit}
            loading={submitting}
            onClick={submit}
            className="w-full"
          >
            {submitting ? "Starting run…" : "Build plugin"}
          </Button>
        </div>
      </Panel>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* During / after a run.                                                      */
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
  const [actionError, setActionError] = useState<string | null>(null);

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
    setActionError(null);
    try {
      await fn();
      onResumed();
    } catch (err) {
      // Don't leave a dead button: surface why the resume failed (e.g. the
      // run isn't live after an API restart, or the source is unreachable).
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <StageRail variant="live" currentStage={currentStage} status={status} />
        <p className="mt-4 font-mono text-[11px] text-text-faint">
          <span className="text-text-muted">{sourceLabel || "your data"}</span> · run {runId}
        </p>
      </div>

      <PipelineConsole events={events} status={status} currentStage={currentStage} runId={runId} />

      {actionError && (
        <p className="rounded-[var(--radius-sm)] border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
          {actionError}
        </p>
      )}

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
        <Panel eyebrow="Your move" title="Unresolved metric inputs">
          <BindingEditor
            unresolvedRoles={unresolvedRoles}
            submitting={busy}
            onSubmit={(o) => guard(() => setBindingOverrides(runId, o))}
          />
        </Panel>
      )}

      {status === "failed" && (
        <ErrorState
          title={`It stopped at "${currentStage ?? "an early stage"}".`}
          description="The console above has the full trace — the last stage shown is where the error was raised."
        />
      )}

      {status === "succeeded" && (
        <PluginResult
          runId={runId}
          events={events}
          report={report}
          tokenUsage={detail?.token_usage ?? null}
          packFit={detail?.pack_fit ?? null}
          knowledgePack={detail?.knowledge_pack ?? null}
          warehouseSchema={detail?.warehouse_schema ?? null}
        />
      )}

      {report && status !== "succeeded" && <ValidationReportView report={report} />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Low-confidence industry pick.                                             */
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
      <Panel>
        <p className="text-[13px] text-warn">Waiting on the match scores…</p>
      </Panel>
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
    <Panel eyebrow="Your move" title="Which industry fits?">
      <p className="text-[13px] leading-relaxed text-text-muted">
        No single pack matched confidently. Pick the one whose signals actually describe your data.
        {promoteGeneric && " generic-analytics makes no value assumptions and is the safe choice."}
      </p>

      <ul className="mt-4 divide-y divide-border">
        {ordered.map((m) => {
          const safe = promoteGeneric && m.pack_slug === "generic-analytics";
          return (
            <li key={m.pack_slug} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[13px] text-text">{m.pack_slug}</span>
                  <span className="font-mono text-[12px] tabular-nums text-text-faint">
                    {Math.round(m.confidence * 100)}%
                  </span>
                  {safe && (
                    <span className="rounded-[5px] bg-ok/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-ok">
                      recommended
                    </span>
                  )}
                </div>
                {m.matched_signals.length > 0 && (
                  <div className="mt-0.5 truncate text-[12px] text-text-faint">
                    matched on {m.matched_signals.join(", ")}
                  </div>
                )}
              </div>
              <Button
                type="button"
                variant={safe ? "primary" : "secondary"}
                size="sm"
                disabled={submitting}
                onClick={() => onConfirm(m.pack_slug)}
                className="shrink-0"
              >
                Use this
              </Button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
