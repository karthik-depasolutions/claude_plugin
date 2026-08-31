import { downloadUrl } from "../lib/api";
import type { StageEvent, ValidationReport } from "../lib/types";
import PublishPanel from "./PublishPanel";
import WarehouseCredentialsPanel from "./WarehouseCredentialsPanel";

interface Props {
  runId: string;
  events: StageEvent[];
  report: ValidationReport | null;
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <div>
      <div className="font-display text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs uppercase tracking-wide text-doc-ink/50">{label}</div>
    </div>
  );
}

export default function PluginResult({ runId, events, report }: Props) {
  const last = (stage: string) => [...events].reverse().find((e) => e.stage === stage);
  const pkg = [...events].reverse().find((e) => e.stage === "package" && e.data.plugin_dir);
  const pluginName = String(pkg?.data.plugin_dir ?? "")
    .split(/[\\/]/)
    .filter(Boolean)
    .pop();

  const tableCount = (last("ingest")?.data.tables as unknown[] | undefined)?.length ?? "—";
  const kpiMatch = /Compiled\s+(\d+)/.exec(last("compile_kpis")?.message ?? "");
  const kpiCount = kpiMatch ? kpiMatch[1] : "—";
  const passed = report ? report.checks.filter((c) => c.status === "pass").length : 0;
  const total = report ? report.checks.length : 0;

  return (
    <section className="rounded-xl bg-doc p-6 text-doc-ink shadow-lg shadow-black/20 sm:p-8">
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-jade">Build complete</p>
      <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-tight">
        {pluginName ?? "Your plugin is ready"}
      </h2>
      <p className="mt-1 font-mono text-xs text-doc-ink/55">run {runId}</p>

      <div className="mt-6 grid grid-cols-3 gap-4 border-y border-doc-hair py-5">
        <Stat value={tableCount} label="tables" />
        <Stat value={kpiCount} label="metrics" />
        <Stat value={`${passed}/${total}`} label="checks passed" />
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <a
          href={downloadUrl(runId)}
          className="rounded-md bg-jade px-4 py-2 text-sm font-medium text-doc-ink transition-opacity hover:opacity-90"
        >
          Download plugin (.zip)
        </a>
        <span className="text-xs text-doc-ink/55">
          Then <code className="rounded bg-doc-ink/5 px-1 py-0.5 font-mono">claude --plugin-dir ./{pluginName}</code>
        </span>
      </div>

      <div className="mt-6 space-y-4">
        <WarehouseCredentialsPanel runId={runId} />
        <PublishPanel runId={runId} defaultRepoName={pluginName} />
      </div>
    </section>
  );
}
