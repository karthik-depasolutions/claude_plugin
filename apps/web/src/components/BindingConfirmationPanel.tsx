import { useState } from "react";
import type { BindingQuestion } from "../lib/types";

interface Props {
  questions: BindingQuestion[];
  onSubmit: (confirmations: Record<string, string>) => void;
  submitting: boolean;
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const cls =
    pct >= 80
      ? "border-physical/40 bg-physical/10 text-physical"
      : pct >= 50
        ? "border-attention/40 bg-attention/10 text-attention"
        : "border-danger/40 bg-danger/10 text-danger";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {pct}% confidence
    </span>
  );
}

/** "Decline / skip affected KPIs" sentinel value in the confirmations map. */
const DECLINE = "__decline__";

export default function BindingConfirmationPanel({ questions, onSubmit, submitting }: Props) {
  const [selections, setSelections] = useState<Record<string, string>>(() =>
    Object.fromEntries(questions.map((q) => [q.role, q.physical]))
  );

  function select(role: string, value: string) {
    setSelections((prev) => ({ ...prev, [role]: value }));
  }

  function handleSubmit() {
    const confirmations: Record<string, string> = {};
    for (const [role, value] of Object.entries(selections)) {
      if (value !== DECLINE) {
        confirmations[role] = value;
      }
    }
    onSubmit(confirmations);
  }

  return (
    <div className="animate-reveal-up space-y-4 rounded-lg border border-canonical/30 bg-canonical/5 p-4">
      <div className="flex items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-canonical/15 text-canonical">
          <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.75}>
            <path d="M10 3v2M10 15v2M3 10h2M15 10h2M5.6 5.6l1.4 1.4M12.9 12.9l1.5 1.5M5.6 14.4l1.4-1.4M12.9 7.1l1.5-1.5" strokeLinecap="round" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-paper">Column mapping needs your confirmation</p>
          <p className="text-xs text-muted">
            These columns were matched with lower confidence. Confirm or correct them to unlock the
            KPIs that depend on them.
          </p>
        </div>
      </div>

      <div className="space-y-5">
        {questions.map((q) => {
          const allChoices = [
            q.physical,
            ...q.alternatives.map(([col]) => col),
          ].filter((v, i, arr) => arr.indexOf(v) === i); // dedupe

          return (
            <div key={q.role} className="space-y-2.5 rounded border border-line bg-[#0E121B] p-3">
              {/* Role + confidence */}
              <div className="flex flex-wrap items-center gap-2">
                <code className="rounded bg-canonical/10 px-1.5 py-0.5 text-xs font-mono text-canonical">
                  {q.role}
                </code>
                <ConfidenceBadge confidence={q.confidence} />
              </div>

              {/* Question text */}
              <p className="text-sm text-paper/90">{q.question}</p>

              {/* Evidence */}
              {q.evidence && (
                <p className="text-xs text-muted italic">{q.evidence}</p>
              )}

              {/* Affected KPIs */}
              {q.kpis_affected.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  <span className="text-[11px] text-muted">Affects:</span>
                  {q.kpis_affected.map((kpi) => (
                    <span
                      key={kpi}
                      className="rounded border border-line px-1.5 py-0.5 text-[11px] text-muted/80"
                    >
                      {kpi}
                    </span>
                  ))}
                </div>
              )}

              {/* Choice chips */}
              <div className="flex flex-wrap gap-2">
                {allChoices.map((col) => {
                  const conf = col === q.physical
                    ? q.confidence
                    : (q.alternatives.find(([c]) => c === col)?.[1] ?? 0);
                  const active = selections[q.role] === col;
                  return (
                    <button
                      key={col}
                      type="button"
                      onClick={() => select(q.role, col)}
                      className={`rounded border px-2.5 py-1 text-xs font-mono transition-colors ${
                        active
                          ? "border-physical/50 bg-physical/15 text-physical"
                          : "border-line text-muted hover:border-canonical/40 hover:text-paper"
                      }`}
                    >
                      {col}
                      {conf > 0 && (
                        <span className="ml-1.5 opacity-60">{Math.round(conf * 100)}%</span>
                      )}
                    </button>
                  );
                })}
                {/* Decline option */}
                <button
                  type="button"
                  onClick={() => select(q.role, DECLINE)}
                  className={`rounded border px-2.5 py-1 text-xs transition-colors ${
                    selections[q.role] === DECLINE
                      ? "border-danger/40 bg-danger/10 text-danger"
                      : "border-line text-muted hover:border-danger/30 hover:text-muted"
                  }`}
                >
                  Skip affected KPIs
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <button
        type="button"
        disabled={submitting}
        onClick={handleSubmit}
        className="rounded bg-canonical px-4 py-2 text-sm font-semibold text-ink transition-transform hover:scale-[1.02] disabled:scale-100 disabled:opacity-40"
      >
        {submitting ? "Resuming…" : "Confirm and continue"}
      </button>
      <p className="text-xs text-muted">
        Declining all removes those KPIs from the generated plugin rather than blocking generation.
      </p>
    </div>
  );
}
