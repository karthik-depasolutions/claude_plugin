import type { ReactNode } from "react";
import { downloadUrl } from "../lib/api";
import type { KnowledgePack, PackFit, StageEvent, TokenUsage, ValidationReport } from "../lib/types";
import DefinitionList from "./ui/DefinitionList";
import Metric from "./ui/Metric";
import Panel from "./ui/Panel";
import StatusBadge from "./ui/StatusBadge";
import PublishPanel from "./PublishPanel";
import WarehouseCredentialsPanel from "./WarehouseCredentialsPanel";

interface Props {
  runId: string;
  events: StageEvent[];
  report: ValidationReport | null;
  tokenUsage?: TokenUsage | null;
  packFit?: PackFit | null;
  knowledgePack?: KnowledgePack | null;
  /** the run's data lives in an isolated Forge warehouse schema (upload or clone) */
  warehouseSchema?: string | null;
}

function compact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

const ROLE_ORDER = ["profiling", "generation", "critique"];

function RoleBreakdown({ byRole }: { byRole: TokenUsage["by_role"] }) {
  const rows = ROLE_ORDER.map((r) => [r, byRole[r]?.total_tokens ?? 0] as const).filter(([, t]) => t > 0);
  if (!rows.length) return null;
  const max = Math.max(...rows.map(([, t]) => t));
  return (
    <div className="mt-5 space-y-1.5">
      {rows.map(([role, tokens]) => (
        <div key={role} className="flex items-center gap-3 text-[12px]">
          <span className="w-20 shrink-0 font-mono text-text-faint">{role}</span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
            <span className="block h-full rounded-full bg-text-muted" style={{ width: `${(tokens / max) * 100}%` }} />
          </span>
          <span className="w-12 shrink-0 text-right font-mono tabular-nums text-text-muted">
            {compact(tokens)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function PluginResult({
  runId,
  events,
  report,
  tokenUsage,
  packFit,
  knowledgePack,
  warehouseSchema,
}: Props) {
  const last = (stage: string) => [...events].reverse().find((e) => e.stage === stage);
  const pkg = [...events].reverse().find((e) => e.stage === "package" && e.data.plugin_dir);
  const pluginName = String(pkg?.data.plugin_dir ?? "")
    .split(/[\\/]/)
    .filter(Boolean)
    .pop();

  const tables = (last("ingest")?.data.tables as unknown[] | undefined) ?? [];
  const tableCount = tables.length || "-";
  const kpiMatch = /Compiled\s+(\d+)/.exec(last("compile_kpis")?.message ?? "");
  const kpiCount = packFit ? String(packFit.kpis_compiled) : kpiMatch ? kpiMatch[1] : "-";
  const kpiLabel =
    packFit && packFit.kpis_skipped > 0
      ? `metrics · ${packFit.kpis_skipped} skipped`
      : packFit?.primary_pack_slug
        ? `metrics · ${packFit.primary_pack_slug}`
        : "metrics";
  const passed = report ? report.checks.filter((c) => c.status === "pass").length : 0;
  const total = report ? report.checks.length : 0;

  const cost = tokenUsage?.estimated_cost_usd ?? 0;
  const costLabel = cost > 0 ? `LLM tokens · ~$${cost.toFixed(cost < 1 ? 3 : 2)}` : "LLM tokens";
  const coverage = knowledgePack?.schema_coverage;
  const cookbookRate = knowledgePack?.cookbook_pass_rate;

  const bindMatch = /Bound\s+(\d+)\s+role/.exec(last("bind")?.message ?? "");
  const unresolved: string[] = last("bind")?.data.unresolved_roles ?? [];
  const detailRows: { term: string; detail: ReactNode }[] = [];
  if (tables.length > 0) {
    detailRows.push({
      term: `source table${tables.length === 1 ? "" : "s"}`,
      detail: <span className="font-mono text-text-muted">{tables.map(String).join(", ")}</span>,
    });
  }
  if (packFit?.primary_pack_slug) {
    detailRows.push({ term: "industry pack", detail: packFit.primary_pack_slug });
  }
  if (bindMatch) {
    detailRows.push({
      term: "roles bound",
      detail: `${bindMatch[1]}${unresolved.length ? ` · ${unresolved.length} unresolved` : ""}`,
    });
  }
  detailRows.push({
    term: "data",
    detail: warehouseSchema ? (
      <>
        isolated copy on the Forge warehouse{" "}
        <span className="font-mono text-text-faint">({warehouseSchema})</span>
      </>
    ) : (
      "read live from the source — no copy stored"
    ),
  });

  return (
    <Panel
      eyebrow="Build complete"
      title={pluginName ?? "Your plugin is ready"}
      actions={
        <>
          <span className="hidden font-mono text-[11px] text-text-faint sm:inline">run {runId}</span>
          <StatusBadge status="succeeded" />
        </>
      }
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-3 lg:grid-cols-5">
        <Metric value={tableCount} label="tables" />
        <Metric value={kpiCount} label={kpiLabel} />
        <Metric value={`${passed}/${total}`} label="checks passed" />
        <Metric
          value={tokenUsage ? compact(tokenUsage.total_tokens) : "-"}
          label={tokenUsage ? costLabel : "LLM tokens"}
        />
        <Metric
          value={coverage != null ? `${Math.round(coverage * 100)}%` : "-"}
          label={
            cookbookRate != null
              ? `column coverage · cookbook ${Math.round(cookbookRate * 100)}%`
              : "column coverage"
          }
        />
      </div>

      {tokenUsage ? <RoleBreakdown byRole={tokenUsage.by_role} /> : null}

      {detailRows.length > 0 && (
        <div className="mt-6 border-t border-border pt-5">
          <p className="font-eyebrow text-[10px] font-medium uppercase tracking-[0.16em] text-text-faint">
            What ran
          </p>
          <DefinitionList className="mt-3" termWidth="sm:w-44" rows={detailRows} />
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-border pt-5">
        <a
          href={downloadUrl(runId)}
          className="inline-flex h-9 items-center rounded-[var(--radius-sm)] bg-accent px-3.5 text-[13px] font-medium text-accent-ink transition-colors hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          Download plugin (.zip)
        </a>
        <span className="text-[12px] text-text-faint">
          then{" "}
          <code className="rounded-[5px] bg-surface-2 px-1.5 py-0.5 font-mono text-text-muted">
            claude --plugin-dir ./{pluginName}
          </code>
        </span>
      </div>

      <div className="mt-4 space-y-3">
        <WarehouseCredentialsPanel runId={runId} />
        <PublishPanel runId={runId} defaultRepoName={pluginName} />
      </div>
    </Panel>
  );
}
