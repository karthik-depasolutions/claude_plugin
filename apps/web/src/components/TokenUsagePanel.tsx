/** What this plugin cost to build, in tokens.
 *
 * Every LLM call in a run is accounted for: the provider path (profiling,
 * generation, self-critique) and the LangChain agents. The breakdown matters
 * more than the total — it's the difference between "the build was expensive"
 * and "the context-discovery agent did most of the thinking", which is the
 * thing a user can actually act on. */

import type { TokenUsage } from "../lib/types";

interface Props {
  usage: TokenUsage;
}

/** Component keys come from the orchestrator's billing calls. Anything not
 *  listed still renders, using its raw key — a new agent shows up as soon as
 *  it starts spending, rather than silently vanishing from the total. */
const COMPONENT_META: Record<string, { label: string; hint: string; bar: string }> = {
  profiling: {
    label: "Profiling",
    hint: "Reading the shape and meaning of your columns",
    bar: "bg-canonical",
  },
  context_discovery: {
    label: "Context discovery",
    hint: "Working out what your data represents in business terms",
    bar: "bg-physical",
  },
  data_understanding: {
    label: "Data understanding",
    hint: "Resolving ambiguous columns against real values",
    bar: "bg-physical",
  },
  binding: {
    label: "Schema binding",
    hint: "Matching your columns to the metrics they can support",
    bar: "bg-attention",
  },
  understanding: {
    label: "Column enrichment",
    hint: "Follow-up investigation of unclear columns",
    bar: "bg-canonical",
  },
  generation: {
    label: "Generation",
    hint: "Writing the plugin's skills, commands, and agent",
    bar: "bg-canonical",
  },
  critique: {
    label: "Self-critique",
    hint: "Reviewing the generated plugin for errors",
    bar: "bg-muted",
  },
};

function titleCase(key: string): string {
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export default function TokenUsagePanel({ usage }: Props) {
  const total = usage.input_tokens + usage.output_tokens;
  if (total === 0) return null;

  const rows = Object.entries(usage.by_component)
    .map(([key, value]) => ({
      key,
      meta: COMPONENT_META[key] ?? { label: titleCase(key), hint: "", bar: "bg-muted" },
      total: value.input_tokens + value.output_tokens,
      calls: value.llm_calls,
    }))
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total);

  const largest = rows.length > 0 ? rows[0].total : 1;

  return (
    <div className="animate-reveal-up space-y-4 rounded-lg border border-line bg-[#0E121B] p-4">
      <div className="flex items-center gap-2.5 border-b border-line pb-3">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-canonical/15 text-canonical">
          <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.75}>
            <path d="M3 16V9M8 16V5M13 16v-4M18 16V7" strokeLinecap="round" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-paper">What this plugin cost to build</p>
          <p className="text-[11px] text-muted">
            Tokens used across every AI step of this run
          </p>
        </div>
      </div>

      {/* Headline figures */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total tokens" value={total.toLocaleString()} accent />
        <Stat label="Input" value={usage.input_tokens.toLocaleString()} />
        <Stat
          label="Output"
          value={usage.output_tokens.toLocaleString()}
          note={
            usage.thinking_tokens > 0
              ? `includes ${usage.thinking_tokens.toLocaleString()} reasoning`
              : undefined
          }
        />
        <Stat label="Model calls" value={String(usage.llm_calls)} />
      </div>

      {/* Per-step breakdown */}
      {rows.length > 0 && (
        <div className="space-y-2 border-t border-line pt-3">
          {rows.map((row) => {
            const pct = Math.round((row.total / total) * 100);
            return (
              <div key={row.key} className="space-y-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs font-medium text-paper" title={row.meta.hint}>
                    {row.meta.label}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted">
                    {row.total.toLocaleString()}
                    <span className="ml-1.5 text-muted/60">{pct}%</span>
                  </span>
                </div>
                <div className="h-1 overflow-hidden rounded-full bg-line">
                  <div
                    className={`h-full rounded-full transition-all ${row.meta.bar}`}
                    style={{ width: `${Math.max(2, Math.round((row.total / largest) * 100))}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  note,
  accent,
}: {
  label: string;
  value: string;
  note?: string;
  accent?: boolean;
}) {
  return (
    <div className="rounded-md border border-line bg-ink px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-muted">{label}</p>
      <p
        className={`font-mono text-sm tabular-nums ${accent ? "text-canonical" : "text-paper"}`}
      >
        {value}
      </p>
      {note && <p className="text-[10px] text-muted/70">{note}</p>}
    </div>
  );
}
