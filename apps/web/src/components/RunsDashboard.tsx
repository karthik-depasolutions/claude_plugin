import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listRuns, downloadUrl, cancelRun, deleteRun } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { RunSummary } from "../lib/types";

interface RunsDashboardProps {
  onSelectRun: (runId: string) => void;
  onNewRun: () => void;
}

/** Compact token count for a dense card row; the exact figure is in the
 *  title attribute so nothing is lost to rounding. */
export function formatTokens(total: number): string {
  if (total < 1_000) return String(total);
  if (total < 1_000_000) return `${(total / 1_000).toFixed(total < 10_000 ? 1 : 0)}K`;
  return `${(total / 1_000_000).toFixed(1)}M`;
}

export default function RunsDashboard({ onSelectRun, onNewRun }: RunsDashboardProps) {
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);

  const [adminScope, setAdminScope] = useState<"all" | "mine">("all");
  const [tab, setTab] = useState<"all" | "in_progress" | "completed" | "failed">("all");
  const [search, setSearch] = useState("");

  const effectiveScope = isAdmin ? adminScope : "mine";

  const { data: runs, isLoading, refetch } = useQuery({
    queryKey: ["runs", effectiveScope],
    queryFn: () => listRuns(effectiveScope),
    refetchInterval: 4000, // auto-refresh to catch background pipeline updates
  });

  async function handleCancelRun(runId: string) {
    if (!window.confirm("Are you sure you want to stop and cancel this plugin build?")) return;
    try {
      await cancelRun(runId);
      refetch();
    } catch (err) {
      alert("Failed to cancel run: " + err);
    }
  }

  async function handleDeleteRun(runId: string) {
    if (!window.confirm("Are you sure you want to delete this run from history?")) return;
    try {
      await deleteRun(runId);
      refetch();
    } catch (err) {
      alert("Failed to delete run: " + err);
    }
  }

  const allRuns = runs ?? [];

  const inProgressRuns = allRuns.filter(
    (r) => r.status === "needs_input" || r.status === "running" || r.status === "pending"
  );
  const completedRuns = allRuns.filter((r) => r.status === "succeeded");
  const failedRuns = allRuns.filter((r) => r.status === "failed");

  const filteredRuns = allRuns.filter((r) => {
    if (tab === "in_progress" && r.status !== "needs_input" && r.status !== "running" && r.status !== "pending") {
      return false;
    }
    if (tab === "completed" && r.status !== "succeeded") {
      return false;
    }
    if (tab === "failed" && r.status !== "failed") {
      return false;
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      const matchLabel = r.label?.toLowerCase().includes(q);
      const matchIndustry = r.industry?.toLowerCase().includes(q);
      const matchId = r.run_id.toLowerCase().includes(q);
      const matchTenant = r.tenant_id?.toLowerCase().includes(q);
      const matchTables = r.tables?.some((t) => t.toLowerCase().includes(q));
      return matchLabel || matchIndustry || matchId || matchTenant || matchTables;
    }
    return true;
  });

  function formatTime(iso?: string | null) {
    if (!iso) return "Recently";
    try {
      const date = new Date(iso);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return "Just now";
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      const diffDays = Math.floor(diffHours / 24);
      if (diffDays < 7) return `${diffDays}d ago`;
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch {
      return "Recently";
    }
  }

  return (
    <div className="space-y-6">
      {/* Admin Scope Control Banner */}
      {isAdmin && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-950/20 px-4 py-2.5 text-xs text-amber-200">
          <div className="flex items-center gap-2">
            <span className="text-base">👑</span>
            <span className="font-semibold text-amber-300">Admin Control Panel:</span>
            <span className="text-amber-200/80">
              {adminScope === "all"
                ? "Showing all users' pending generations and completed plugins across the platform."
                : "Showing only your own plugins and runs."}
            </span>
          </div>

          <div className="flex items-center rounded-lg border border-amber-500/40 bg-ink/80 p-0.5">
            <button
              type="button"
              onClick={() => setAdminScope("all")}
              className={`rounded px-2.5 py-1 text-[11px] font-semibold transition-colors ${adminScope === "all" ? "bg-amber-400 text-ink shadow" : "text-muted hover:text-paper"
                }`}
            >
              All Platform Runs
            </button>
            <button
              type="button"
              onClick={() => setAdminScope("mine")}
              className={`rounded px-2.5 py-1 text-[11px] font-semibold transition-colors ${adminScope === "mine" ? "bg-amber-400 text-ink shadow" : "text-muted hover:text-paper"
                }`}
            >
              My Runs Only
            </button>
          </div>
        </div>
      )}

      {/* Top Banner: Metric Highlights & Action */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <div className="rounded-xl border border-line bg-[#0E121B]/80 p-4">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted">
            {isAdmin && adminScope === "all" ? "All Platform Builds" : "My Total Builds"}
          </div>
          <div className="mt-1 font-display text-2xl font-bold text-paper">{allRuns.length}</div>
          <div className="mt-1 text-[11px] text-muted">Historical runs & pipelines</div>
        </div>

        <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-amber-300">
              {isAdmin && adminScope === "all" ? "All Pending Generations" : "Action Required"}
            </span>
            {inProgressRuns.length > 0 && (
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75"></span>
                <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500"></span>
              </span>
            )}
          </div>
          <div className="mt-1 font-display text-2xl font-bold text-amber-400">{inProgressRuns.length}</div>
          <div className="mt-1 text-[11px] text-amber-300/70">Paused or awaiting confirmation</div>
        </div>

        <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
          <div className="text-[11px] font-medium uppercase tracking-wider text-emerald-300">Completed Plugins</div>
          <div className="mt-1 font-display text-2xl font-bold text-emerald-400">{completedRuns.length}</div>
          <div className="mt-1 text-[11px] text-emerald-300/70">Validated & ready to install</div>
        </div>

        <div className="flex flex-col justify-between rounded-xl border border-canonical/30 bg-canonical/10 p-4">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wider text-canonical">New Integration</div>
            <div className="mt-0.5 text-xs text-paper/80">Connect dataset or warehouse</div>
          </div>
          <button
            type="button"
            onClick={onNewRun}
            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg bg-canonical px-3 py-2 text-xs font-semibold text-ink shadow transition-all hover:scale-[1.02] hover:brightness-105 active:scale-[0.98]"
          >
            <span>+</span> Start New Plugin
          </button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col gap-3 rounded-xl border border-line bg-[#0E121B]/90 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1 overflow-x-auto">
          <button
            type="button"
            onClick={() => setTab("all")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${tab === "all" ? "bg-canonical text-ink" : "text-muted hover:bg-ink hover:text-paper"
              }`}
          >
            All Builds ({allRuns.length})
          </button>
          <button
            type="button"
            onClick={() => setTab("in_progress")}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${tab === "in_progress" ? "bg-amber-500 text-ink font-semibold" : "text-muted hover:bg-ink hover:text-paper"
              }`}
          >
            {isAdmin && adminScope === "all" ? "Pending Generations" : "Action Required"}
            {inProgressRuns.length > 0 && (
              <span className="rounded-full bg-amber-400/20 px-1.5 py-0.2 text-[10px] text-amber-200">
                {inProgressRuns.length}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => setTab("completed")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${tab === "completed" ? "bg-emerald-500 text-ink font-semibold" : "text-muted hover:bg-ink hover:text-paper"
              }`}
          >
            Completed ({completedRuns.length})
          </button>
          <button
            type="button"
            onClick={() => setTab("failed")}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${tab === "failed" ? "bg-danger text-paper" : "text-muted hover:bg-ink hover:text-paper"
              }`}
          >
            Failed ({failedRuns.length})
          </button>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder={isAdmin ? "Search by project, user email, table..." : "Search by project, table, industry..."}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-line bg-ink px-3 py-1.5 text-xs text-paper placeholder:text-muted/60 transition-colors focus:border-canonical focus:outline-none sm:w-64"
          />
          <button
            type="button"
            onClick={() => refetch()}
            title="Refresh history"
            className="rounded-lg border border-line bg-ink px-2.5 py-1.5 text-xs text-muted hover:text-paper"
          >
            ↻
          </button>
        </div>
      </div>

      {/* Runs List Content */}
      {isLoading ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-line bg-[#0E121B]/40 py-16 text-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-canonical border-t-transparent"></div>
          <p className="mt-3 text-xs text-muted">Loading plugin builds…</p>
        </div>
      ) : filteredRuns.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-[#0E121B]/40 py-16 px-6 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-ink text-xl">
            📦
          </div>
          <h3 className="mt-3 font-display text-sm font-semibold text-paper">
            {search ? "No matching plugin builds" : "No plugin builds found"}
          </h3>
          <p className="mt-1 max-w-sm text-xs text-muted">
            {search
              ? "Try adjusting your search query or switching filters."
              : "Upload a CSV, Excel sheet, or connect your database to generate your first custom Claude Desktop plugin."}
          </p>
          {!search && (
            <button
              type="button"
              onClick={onNewRun}
              className="mt-5 rounded-lg bg-canonical px-4 py-2 text-xs font-semibold text-ink shadow transition-transform hover:scale-105"
            >
              Start New Plugin Build
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {filteredRuns.map((run) => (
            <RunCard
              key={run.run_id}
              run={run}
              isAdminView={isAdmin && adminScope === "all"}
              timeAgo={formatTime(run.created_at)}
              onSelect={() => onSelectRun(run.run_id)}
              onCancel={() => handleCancelRun(run.run_id)}
              onDelete={() => handleDeleteRun(run.run_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function RunCard({
  run,
  isAdminView,
  timeAgo,
  onSelect,
  onCancel,
  onDelete,
}: {
  run: RunSummary;
  isAdminView: boolean;
  timeAgo: string;
  onSelect: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const isNeedsInput = run.status === "needs_input";
  const isRunning = run.status === "running" || run.status === "pending";
  const isSucceeded = run.status === "succeeded";
  const isFailed = run.status === "failed";

  return (
    <div
      onClick={onSelect}
      className={`group relative flex flex-col justify-between rounded-xl border p-4.5 transition-all cursor-pointer hover:shadow-xl sm:flex-row sm:items-center ${
        isNeedsInput
          ? "border-amber-500/40 bg-amber-950/10 hover:border-amber-400/70 hover:bg-amber-950/20"
          : isRunning
          ? "border-cyan-500/40 bg-cyan-950/10 hover:border-cyan-400/70"
          : isSucceeded
          ? "border-line bg-[#0E121B] hover:border-emerald-500/40 hover:bg-[#111722]"
          : "border-danger/30 bg-danger/5 hover:border-danger/50"
      }`}
    >
      {/* Left side: Project name, user email (admin view), industry, tables, date */}
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-display text-sm font-semibold tracking-tight text-paper group-hover:text-canonical transition-colors">
            {run.label || `Plugin #${run.run_id.slice(0, 8)}`}
          </span>

          {isAdminView && run.tenant_id && (
            <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-mono text-amber-300">
              👤 {run.tenant_id}
            </span>
          )}

          {run.industry && (
            <span className="rounded-full border border-line bg-ink px-2 py-0.5 text-[10px] font-mono text-muted uppercase">
              {run.industry}
            </span>
          )}

          {/* Status Badge */}
          {isNeedsInput && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/20 px-2.5 py-0.5 text-[10px] font-semibold text-amber-300">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400"></span>
              Action Required ({run.current_stage || "review"})
            </span>
          )}

          {isRunning && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/40 bg-cyan-500/20 px-2.5 py-0.5 text-[10px] font-semibold text-cyan-300">
              <span className="h-1.5 w-1.5 animate-ping rounded-full bg-cyan-400"></span>
              Running ({run.current_stage || "pipeline"})
            </span>
          )}

          {isSucceeded && (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[10px] font-medium text-emerald-400">
              ✓ Ready
            </span>
          )}

          {isFailed && (
            <span className="inline-flex items-center gap-1 rounded-full border border-danger/30 bg-danger/10 px-2.5 py-0.5 text-[10px] font-medium text-danger">
              ✕ Stopped / Failed
            </span>
          )}
        </div>

        {/* Secondary Info: Tables & Timestamp */}
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          <span className="font-mono text-[11px] text-muted/80">ID: {run.run_id}</span>
          <span>•</span>
          <span>{timeAgo}</span>
          {run.tables && run.tables.length > 0 && (
            <>
              <span>•</span>
              <span className="text-paper/70 font-mono text-[11px]">
                {run.tables.length} {run.tables.length === 1 ? "table" : "tables"} ({run.tables.slice(0, 3).join(", ")}
                {run.tables.length > 3 ? "…" : ""})
              </span>
            </>
          )}
          {run.kpis_count !== undefined && run.kpis_count !== null && (
            <>
              <span>•</span>
              <span className="text-emerald-400/90 font-medium text-[11px]">
                {run.kpis_count} KPIs Generated
              </span>
            </>
          )}
          {!!run.total_tokens && (
            <>
              <span>•</span>
              <span
                className="font-mono text-[11px] text-canonical/90"
                title={`${run.total_tokens.toLocaleString()} tokens across ${run.llm_calls ?? 0} model call(s)`}
              >
                {formatTokens(run.total_tokens)} tokens
              </span>
            </>
          )}
        </div>

        {/* Error message snippet if failed */}
        {isFailed && run.error && (
          <p className="mt-1 text-[11px] text-danger/90 line-clamp-1 font-mono">
            {run.error}
          </p>
        )}
      </div>

      {/* Right side: Action Button */}
      <div className="mt-3 flex items-center gap-2 sm:mt-0">
        {(isRunning || isNeedsInput) && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onCancel();
            }}
            className="flex items-center gap-1 rounded-lg border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-xs font-semibold text-danger hover:bg-danger/20 transition-colors"
            title="Stop / Cancel execution"
          >
            <span>⏹ Stop</span>
          </button>
        )}

        {isNeedsInput && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="flex items-center gap-1 rounded-lg bg-amber-400 px-3.5 py-1.5 text-xs font-bold text-ink shadow-lg shadow-amber-500/20 transition-all hover:scale-105 hover:bg-amber-300"
          >
            <span>Resume</span>
            <span>→</span>
          </button>
        )}

        {isRunning && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="flex items-center gap-1 rounded-lg bg-cyan-500 px-3.5 py-1.5 text-xs font-semibold text-ink shadow transition-all hover:scale-105"
          >
            <span>View Live</span>
            <span>→</span>
          </button>
        )}

        {isSucceeded && (
          <div className="flex items-center gap-2">
            <a
              href={downloadUrl(run.run_id)}
              download
              onClick={(e) => e.stopPropagation()}
              className="rounded-lg border border-line bg-ink px-3 py-1.5 text-xs font-medium text-paper/80 transition-colors hover:border-canonical hover:text-canonical"
            >
              Download ZIP
            </a>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onSelect();
              }}
              className="flex items-center gap-1 rounded-lg bg-line/80 px-3 py-1.5 text-xs font-medium text-paper transition-colors hover:bg-paper hover:text-ink"
            >
              <span>View Plugin</span>
              <span>→</span>
            </button>
          </div>
        )}

        {isFailed && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger transition-colors hover:bg-danger hover:text-paper"
          >
            View Logs
          </button>
        )}

        {/* Delete Run Button */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="rounded-lg border border-line bg-ink p-1.5 text-xs text-muted transition-colors hover:border-danger/40 hover:text-danger"
          title="Delete run from history"
        >
          🗑
        </button>
      </div>
    </div>
  );
}
