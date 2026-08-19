import { useState } from "react";
import type { DataReview, FindingSeverity, IndustryGuess, RankedMatch } from "../lib/types";

interface Props {
  review: DataReview;
  needsAnswers: boolean;
  needsIndustry: boolean;
  matches: RankedMatch[];
  industryGuess?: IndustryGuess;
  onSubmit: (answers: Record<string, string>, industry?: string) => void;
  submitting: boolean;
}

const SEVERITY_META: Record<
  FindingSeverity,
  { label: string; dot: string; pill: string; bar: string }
> = {
  high: {
    label: "High",
    dot: "bg-red-400",
    pill: "border-red-500/40 bg-red-500/10 text-red-300",
    bar: "bg-red-400",
  },
  medium: {
    label: "Medium",
    dot: "bg-amber-400",
    pill: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    bar: "bg-amber-400",
  },
  low: {
    label: "Low",
    dot: "bg-slate-500",
    pill: "border-slate-600/60 bg-slate-800/60 text-slate-400",
    bar: "bg-slate-500",
  },
};

const SEVERITY_ORDER: FindingSeverity[] = ["high", "medium", "low"];

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M5 10.5 8.5 14 15 6.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FlagIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.75}>
      <path
        d="M5 16.5V3.75c0-1.5 1.1-1.85 1.9-1.3l1.3.9c.75.5 1.85.4 2.5-.2l.7-.65c.75-.65 1.85-.55 2.5.2l1.15 1.35c.65.75 1.75.85 2.5.2v6.5c-.75.65-1.85.55-2.5-.2l-1.15-1.35c-.65-.75-1.75-.85-2.5-.2l-.7.65c-.75.65-1.85.75-2.5.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SeverityTally({ findings }: { findings: DataReview["findings"] }) {
  const counts = SEVERITY_ORDER.map((severity) => ({
    severity,
    count: findings.filter((f) => f.severity === severity).length,
  })).filter((entry) => entry.count > 0);

  if (counts.length === 0) return null;

  return (
    <div className="flex items-center gap-1.5">
      {counts.map(({ severity, count }) => (
        <span
          key={severity}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${SEVERITY_META[severity].pill}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${SEVERITY_META[severity].dot}`} />
          {count} {SEVERITY_META[severity].label.toLowerCase()}
        </span>
      ))}
    </div>
  );
}

export default function DataReviewPanel({
  review,
  needsAnswers,
  needsIndustry,
  matches,
  industryGuess,
  onSubmit,
  submitting,
}: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [chosenPack, setChosenPack] = useState<string | null>(null);
  const [findingsOpen, setFindingsOpen] = useState(review.findings.length > 0);

  const hasAnswers = needsAnswers && review.questions.length > 0;
  const answerCount = Object.values(values).filter((v) => v.trim().length > 0).length;
  const canContinue = (!needsIndustry || chosenPack !== null) && !submitting;

  function submit() {
    const answers = Object.fromEntries(
      Object.entries(values).filter(([, v]) => v.trim().length > 0)
    );
    onSubmit(answers, chosenPack ?? undefined);
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-ink">
      {/* Frame — what this step is */}
      <div className="border-b border-line px-6 py-5">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-canonical">
          Data check · before building
        </p>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-xl font-semibold tracking-tight text-paper">
            Confirm how your data should be read
          </h2>
          <SeverityTally findings={review.findings} />
        </div>
        <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-muted">
          The pipeline analyzed your source and found a few things worth your call before it
          generates the plugin. Answer what you can — skipped questions stay skipped.
        </p>
      </div>

      {review.skipped_tables.length > 0 && (
        <p className="border-b border-line px-6 py-3 text-xs text-muted">
          Skipped value-frequency analysis on {review.skipped_tables.length} table(s) over the
          size ceiling: <span className="font-mono">{review.skipped_tables.join(", ")}</span>
        </p>
      )}

      {/* Findings — what the analyzer flagged */}
      {review.findings.length > 0 && (
        <section className="border-b border-line">
          <button
            type="button"
            onClick={() => setFindingsOpen((v) => !v)}
            className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left"
          >
            <span className="flex items-center gap-2.5 text-sm font-medium text-paper">
              <FlagIcon />
              What the analyzer flagged
            </span>
            <span className="flex items-center gap-2 text-xs text-muted">
              {findingsOpen ? "Hide details" : "Show details"}
              <svg
                viewBox="0 0 20 20"
                className={`h-3.5 w-3.5 transition-transform duration-200 ${findingsOpen ? "rotate-180" : ""}`}
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path d="M5 7.5 10 12.5 15 7.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </button>

          {findingsOpen && (
            <ul className="space-y-2.5 px-6 pb-5">
              {review.findings.map((finding, index) => {
                const meta = SEVERITY_META[finding.severity];
                return (
                  <li
                    key={finding.id}
                    className="relative overflow-hidden rounded-lg border border-line bg-[#10141d] pl-3"
                  >
                    <span className={`absolute inset-y-0 left-0 w-0.5 ${meta.bar}`} />
                    <div className="px-3 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${meta.pill}`}
                        >
                          <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                          {meta.label}
                        </span>
                        <span className="font-mono text-xs text-paper">
                          {finding.table}.{finding.column}
                        </span>
                        <span className="font-mono text-[10px] text-muted">{finding.code}</span>
                        {index === 0 && (
                          <span className="rounded-full border border-canonical/40 bg-canonical/10 px-2 py-0.5 text-[10px] font-medium text-canonical">
                            most relevant
                          </span>
                        )}
                      </div>
                      <p className="mt-1.5 text-sm leading-relaxed text-paper/80">{finding.summary}</p>
                      {finding.top_values.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {finding.top_values.map((tv) => (
                            <span
                              key={tv.value}
                              className="rounded border border-line bg-line/60 px-1.5 py-0.5 font-mono text-[11px] text-paper/70"
                            >
                              {tv.value} <span className="text-muted">· {tv.percent}%</span>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {/* Questions — the human's take */}
      {hasAnswers && (
        <section className="border-b border-line px-6 py-5">
          <div className="mb-4">
            <p className="text-sm font-medium text-paper">Your take on a few things</p>
            <p className="mt-0.5 text-xs leading-relaxed text-muted">
              The analyzer paused here because these answers change how the plugin reads your
              columns. Answer what you can — each field is optional.
            </p>
          </div>

          <div className="space-y-4">
            {review.questions.map((question, index) => (
              <div key={question.id} className="rounded-lg border border-line bg-[#10141d] p-4">
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-canonical/15 font-mono text-[11px] font-medium text-canonical">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <label className="block text-sm font-medium leading-snug text-paper">
                      {question.question}
                    </label>
                    {question.context && (
                      <p className="mt-1 text-xs leading-relaxed text-muted">{question.context}</p>
                    )}
                    <textarea
                      value={values[question.id] ?? ""}
                      onChange={(e) => setValues((v) => ({ ...v, [question.id]: e.target.value }))}
                      rows={2}
                      placeholder="Optional — share what you know…"
                      className="mt-2.5 w-full resize-y rounded-lg border border-line bg-ink px-3 py-2 text-sm text-paper placeholder:text-muted/60 focus:border-canonical/70 focus:outline-none focus:ring-1 focus:ring-canonical/40"
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Industry — the human's pick */}
      {needsIndustry && (
        <section className="border-b border-line px-6 py-5">
          <div className="mb-4">
            <p className="text-sm font-medium text-paper">Pick the industry match</p>
            <p className="mt-0.5 text-xs leading-relaxed text-muted">
              Auto-classification couldn't settle on one. Choose the closest fit — this sets the
              schema roles, KPIs, and vocabulary the plugin is built around.
            </p>
          </div>
          {industryGuess && industryGuess.reasoning && (
            <div className="mb-4 rounded-lg border border-canonical/30 bg-canonical/5 px-3.5 py-3 text-xs leading-relaxed text-paper/80">
              <span className="font-medium text-canonical">AI's read of the data:</span>{" "}
              {industryGuess.pack_slug_guess && (
                <>
                  likely <span className="font-mono">{industryGuess.pack_slug_guess}</span>{" "}
                  ({Math.round(industryGuess.confidence * 100)}% confidence) —{" "}
                </>
              )}
              {industryGuess.reasoning}
            </div>
          )}
          <IndustryPick matches={matches} chosen={chosenPack} onChoose={setChosenPack} />
        </section>
      )}

      {/* Footer — one action */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4">
        <div className="flex items-center gap-2 text-xs text-muted">
          {hasAnswers && (
            <>
              <span className={answerCount > 0 ? "text-physical" : ""}>
                {answerCount === 0
                  ? "No answers yet — all optional"
                  : `${answerCount} of ${review.questions.length} answered`}
              </span>
              <span className="text-line">·</span>
            </>
          )}
          {needsIndustry && !chosenPack && (
            <span className="text-amber-300/80">Pick an industry to continue</span>
          )}
        </div>

        <button
          type="button"
          disabled={!canContinue}
          onClick={submit}
          className="inline-flex items-center gap-2 rounded-lg bg-physical px-5 py-2.5 text-sm font-semibold text-[#06251c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#06251c]/30 border-t-[#06251c]" />
              Resuming…
            </>
          ) : (
            <>
              Confirm and continue
              <CheckIcon />
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function IndustryPick({
  matches,
  chosen,
  onChoose,
}: {
  matches: RankedMatch[];
  chosen: string | null;
  onChoose: (slug: string) => void;
}) {
  const allLowConfidence = matches.every((m) => m.confidence < 0.45);
  const hasGenericFallback = matches.some((m) => m.pack_slug === "generic-analytics");
  const promoteGeneric = allLowConfidence && hasGenericFallback;
  const orderedMatches = promoteGeneric
    ? [
        matches.find((m) => m.pack_slug === "generic-analytics")!,
        ...matches.filter((m) => m.pack_slug !== "generic-analytics"),
      ]
    : matches;

  return (
    <ul className="space-y-2">
      {orderedMatches.map((match) => {
        const isSafeFallback = promoteGeneric && match.pack_slug === "generic-analytics";
        const isChosen = chosen === match.pack_slug;
        const confidence = Math.round(match.confidence * 100);
        return (
          <li key={match.pack_slug}>
            <button
              type="button"
              onClick={() => onChoose(match.pack_slug)}
              aria-pressed={isChosen}
              className={`w-full rounded-lg border px-4 py-3 text-left transition-colors ${
                isChosen
                  ? "border-canonical bg-canonical/10"
                  : "border-line bg-[#10141d] hover:border-slate-600"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-medium text-paper">{match.pack_slug}</span>
                    {isSafeFallback && (
                      <span className="rounded-full border border-physical/40 bg-physical/10 px-2 py-0.5 text-[10px] font-medium text-physical">
                        recommended · safe fallback
                      </span>
                    )}
                  </div>
                  {match.matched_signals.length > 0 && (
                    <p className="mt-0.5 truncate text-xs text-muted">
                      Matched on: {match.matched_signals.join(", ")}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <span className="text-xs tabular-nums text-muted">{confidence}%</span>
                  <span
                    className={`flex h-5 w-5 items-center justify-center rounded-full border ${
                      isChosen
                        ? "border-canonical bg-canonical text-ink"
                        : "border-slate-600 text-transparent"
                    }`}
                  >
                    <CheckIcon />
                  </span>
                </div>
              </div>
              <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-line">
                <div
                  className={`h-full rounded-full ${isChosen ? "bg-canonical" : "bg-slate-600"} transition-[width] duration-300`}
                  style={{ width: `${confidence}%` }}
                />
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}