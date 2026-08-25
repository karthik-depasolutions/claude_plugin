/** Shows the data-understanding agent's read of the uploaded data — only
 * visible when use_agent=true and the agent ran successfully. Purely
 * advisory; none of this is used to drive pipeline decisions automatically. */

interface ColumnUnderstanding {
  table: string;
  column: string;
  proposed_meaning: string;
  role: string; // "dimension" | "measure" | "identifier" | "temporal" | "unknown"
  confidence: number;
  open_question?: string;
}

interface DataUnderstanding {
  summary?: string;
  column_semantics?: ColumnUnderstanding[];
  data_quality_flags?: { issue: string; severity: string }[];
}

interface Props {
  understanding: DataUnderstanding;
}

const ROLE_META: Record<string, { label: string; dot: string }> = {
  dimension: { label: "Dimension", dot: "bg-canonical" },
  measure: { label: "Measure", dot: "bg-physical" },
  identifier: { label: "Identifier", dot: "bg-muted" },
  temporal: { label: "Temporal", dot: "bg-attention" },
  unknown: { label: "Unknown", dot: "bg-danger" },
};

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "bg-physical" : pct >= 50 ? "bg-attention" : "bg-danger";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1 w-14 overflow-hidden rounded-full bg-line">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-muted">{pct}%</span>
    </div>
  );
}

export default function DataUnderstandingPanel({ understanding }: Props) {
  const columns = understanding.column_semantics ?? [];
  const openQuestions = columns.filter((c) => c.open_question && c.confidence < 0.6);

  return (
    <div className="animate-reveal-up space-y-4 rounded-lg border border-line bg-[#0E121B] p-4">
      {/* Header */}
      <div className="flex items-center gap-2.5 border-b border-line pb-3">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-physical/15 text-physical">
          <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.75}>
            <circle cx="10" cy="10" r="7" />
            <path d="M10 7v3l2 2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-paper">What we learned about your data</p>
          {understanding.summary && (
            <p className="mt-0.5 text-xs text-muted">{understanding.summary}</p>
          )}
        </div>
      </div>

      {/* Open question notice */}
      {openQuestions.length > 0 && (
        <div className="flex items-start gap-2 rounded border border-attention/30 bg-attention/5 px-3 py-2">
          <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 text-attention" fill="none" stroke="currentColor" strokeWidth={1.75}>
            <path d="M10 3v2M10 10v2M5.6 5.6l1.4 1.4M12.9 7.1l1.5-1.5" strokeLinecap="round" />
            <circle cx="10" cy="16" r="1" fill="currentColor" stroke="none" />
          </svg>
          <p className="text-xs text-attention">
            <strong>{openQuestions.length}</strong> column
            {openQuestions.length > 1 ? "s" : ""} still need clarification — answer the questions
            below to improve KPI accuracy.
          </p>
        </div>
      )}

      {/* Column table */}
      {columns.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line text-left text-muted">
                <th className="pb-2 pr-4 font-medium">Column</th>
                <th className="pb-2 pr-4 font-medium">Role</th>
                <th className="pb-2 pr-4 font-medium">Meaning</th>
                <th className="pb-2 font-medium">Confidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {columns.map((col) => {
                const meta = ROLE_META[col.role] ?? ROLE_META.unknown;
                return (
                  <tr key={`${col.table}.${col.column}`} className="text-paper/80">
                    <td className="py-2 pr-4 font-mono">
                      <span className="text-muted">{col.table}.</span>
                      <span className="text-paper">{col.column}</span>
                    </td>
                    <td className="py-2 pr-4">
                      <span className="flex items-center gap-1.5">
                        <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                        {meta.label}
                      </span>
                    </td>
                    <td className="py-2 pr-4 max-w-xs">
                      <span className="text-paper/70">{col.proposed_meaning}</span>
                      {col.open_question && col.confidence < 0.6 && (
                        <span className="ml-1 text-attention">·</span>
                      )}
                    </td>
                    <td className="py-2">
                      <ConfidenceBar value={col.confidence} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Quality flags */}
      {(understanding.data_quality_flags ?? []).length > 0 && (
        <div className="space-y-1 border-t border-line pt-3">
          <p className="text-xs font-medium text-muted">Agent observations</p>
          <ul className="space-y-0.5">
            {understanding.data_quality_flags!.map((flag, i) => (
              <li key={i} className="text-xs text-paper/70">
                <span className="text-attention">⚠</span> {flag.issue}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
