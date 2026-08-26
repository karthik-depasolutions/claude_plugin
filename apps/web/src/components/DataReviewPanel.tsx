import { useState, useEffect } from "react";
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
    dot: "bg-danger",
    pill: "border-danger/40 bg-danger/10 text-danger",
    bar: "bg-danger",
  },
  medium: {
    label: "Medium",
    dot: "bg-attention",
    pill: "border-attention/40 bg-attention/10 text-attention",
    bar: "bg-attention",
  },
  low: {
    label: "Low",
    dot: "bg-muted",
    pill: "border-line bg-line text-muted",
    bar: "bg-muted",
  },
};

const SEVERITY_ORDER: FindingSeverity[] = ["high", "medium", "low"];

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth={2.5}>
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
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium ${SEVERITY_META[severity].pill}`}
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
  const [findingsOpen, setFindingsOpen] = useState(false);
  const [currentQIndex, setCurrentQIndex] = useState(0);

  const questions = review.questions || [];
  const hasQuestions = needsAnswers && questions.length > 0;
  const currentQuestion = hasQuestions ? questions[currentQIndex] : null;

  // Auto select default industry guess if confident
  useEffect(() => {
    if (matches.length > 0 && !chosenPack) {
      setChosenPack(matches[0].pack_slug);
    }
  }, [matches, chosenPack]);

  const answerCount = Object.values(values).filter((v) => v.trim().length > 0).length;
  const canContinue = (!needsIndustry || chosenPack !== null) && !submitting;

  function submit() {
    const answers = Object.fromEntries(
      Object.entries(values).filter(([, v]) => v.trim().length > 0)
    );
    onSubmit(answers, chosenPack ?? undefined);
  }

  function handleToggleChoice(questionId: string, choice: string, isMulti: boolean) {
    const currentVal = values[questionId] ?? "";
    if (isMulti) {
      const selected = currentVal.split(",").map((s) => s.trim()).filter(Boolean);
      const next = selected.includes(choice)
        ? selected.filter((s) => s !== choice)
        : [...selected, choice];
      setValues((prev) => ({ ...prev, [questionId]: next.join(", ") }));
    } else {
      setValues((prev) => ({
        ...prev,
        [questionId]: currentVal === choice ? "" : choice,
      }));
    }
  }

  function handleSelectAll(questionId: string, choices: string[]) {
    setValues((prev) => ({ ...prev, [questionId]: choices.join(", ") }));
  }

  function handleClear(questionId: string) {
    setValues((prev) => ({ ...prev, [questionId]: "" }));
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-surface/90 shadow-2xl backdrop-blur-xl transition-all space-y-0">
      {/* Top Header Banner */}
      <div className="border-b border-line bg-gradient-to-r from-canonical/15 via-base to-base px-6 py-5">
        <div className="flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-canonical/15 px-3 py-1 font-mono text-[11px] font-semibold text-canonical border border-canonical/30">
            <span className="h-2 w-2 rounded-full bg-canonical animate-pulse" />
            AI Domain Alignment
          </span>
          <SeverityTally findings={review.findings} />
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="font-display text-xl font-bold tracking-tight text-paper">
              Review Industry & Business Context
            </h2>
            <p className="mt-1 max-w-xl text-xs leading-relaxed text-muted">
              Confirm the domain model and clarify key business outcomes so your plugin delivers exact operational metrics.
            </p>
          </div>
        </div>
      </div>

      {/* STEP 1: Industry Model Selection (FIRST) */}
      <section className="border-b border-line px-6 py-6 bg-surface/40">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-canonical/20 text-canonical font-mono text-[11px] font-bold border border-canonical/40">
                1
              </span>
              <span className="text-xs font-bold uppercase tracking-wider text-canonical">
                Industry Domain Model
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">
              The ontology and KPI formulas are tailored to this industry. Select or confirm below:
            </p>
          </div>

          {chosenPack && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-physical/15 border border-physical/30 px-3 py-1 text-xs font-mono font-bold text-physical">
              <span>Selected:</span>
              <span>{chosenPack}</span>
            </span>
          )}
        </div>

        {/* AI Recommendation Highlight */}
        {industryGuess && industryGuess.reasoning && (
          <div className="mb-4 rounded-xl border border-canonical/30 bg-canonical/10 p-3.5 text-xs leading-relaxed text-paper">
            <span className="font-semibold text-canonical">AI Recommendation:</span>{" "}
            {industryGuess.pack_slug_guess && (
              <span className="font-mono font-bold text-physical">
                {industryGuess.pack_slug_guess} ({Math.round(industryGuess.confidence * 100)}% match) —{" "}
              </span>
            )}
            {industryGuess.reasoning}
          </div>
        )}

        {/* Industry Cards Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          {matches.map((match) => {
            const isSelected = chosenPack === match.pack_slug;
            const isAiPick = industryGuess?.pack_slug_guess === match.pack_slug;
            const effectiveConfidence = isAiPick
              ? Math.max(match.confidence, industryGuess.confidence)
              : match.confidence;
            const displaySignals = isAiPick && industryGuess?.reasoning
              ? [`AI semantic data analysis match`, ...(match.matched_signals || [])]
              : (match.matched_signals || []);

            return (
              <button
                key={match.pack_slug}
                type="button"
                onClick={() => setChosenPack(match.pack_slug)}
                className={`flex flex-col text-left rounded-xl border p-3.5 transition-all cursor-pointer ${
                  isSelected
                    ? "border-physical bg-physical/15 text-paper ring-1 ring-physical/50 shadow-md scale-[1.01]"
                    : "border-line bg-base/60 text-muted hover:border-line hover:bg-base hover:text-paper"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="font-mono font-bold text-sm text-paper truncate">{match.pack_slug}</span>
                    {isAiPick && (
                      <span className="shrink-0 rounded bg-canonical/20 px-1.5 py-0.5 text-[9px] font-mono font-bold text-canonical border border-canonical/30 uppercase tracking-wide">
                        AI Pick
                      </span>
                    )}
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-mono font-bold ${
                      isSelected ? "bg-physical text-ink" : "bg-line text-muted"
                    }`}
                  >
                    {Math.round(effectiveConfidence * 100)}% Match
                  </span>
                </div>
                {displaySignals.length > 0 && (
                  <ul className="mt-2 space-y-0.5 text-[11px] text-muted">
                    {displaySignals.slice(0, 2).map((signal, i) => (
                      <li key={i} className="truncate">• {signal}</li>
                    ))}
                  </ul>
                )}
              </button>
            );
          })}
        </div>
      </section>

      {/* STEP 2: Business Context Questions (AFTER Industry Classification) */}
      {hasQuestions && currentQuestion && (
        <section className="px-6 py-6 border-b border-line bg-base/50">
          <div className="flex items-center justify-between pb-4">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-canonical/20 text-canonical font-mono text-[11px] font-bold border border-canonical/40">
                2
              </span>
              <span className="text-xs font-mono font-bold text-canonical">
                Question {currentQIndex + 1} of {questions.length}
              </span>
              <span className="text-line">·</span>
              <span className="text-[11px] text-muted font-medium">
                {currentQuestion.kind === "business_context" ? "Business Outcome Mapping" : "Data Finding"}
              </span>
            </div>

            {/* Step Dots */}
            <div className="flex items-center gap-1.5">
              {questions.map((q, idx) => {
                const isAnswered = (values[q.id] ?? "").trim().length > 0;
                const isCurrent = idx === currentQIndex;
                return (
                  <button
                    key={q.id}
                    type="button"
                    onClick={() => setCurrentQIndex(idx)}
                    title={`Jump to Question ${idx + 1}`}
                    className={`h-2 rounded-full transition-all ${
                      isCurrent
                        ? "w-6 bg-canonical"
                        : isAnswered
                        ? "w-2 bg-physical"
                        : "w-2 bg-line hover:bg-muted"
                    }`}
                  />
                );
              })}
            </div>
          </div>

          {/* Interactive Question Card */}
          <div className="relative overflow-hidden rounded-2xl border border-line bg-surface/80 p-6 shadow-md transition-all">
            <div className="space-y-4">
              <div>
                <h3 className="font-display text-base font-semibold text-paper leading-snug">
                  {currentQuestion.question}
                </h3>
                {currentQuestion.why_asking && (
                  <div className="mt-1.5 flex items-center gap-1.5 text-xs text-physical font-medium">
                    <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span>{currentQuestion.why_asking}</span>
                  </div>
                )}
              </div>

              {currentQuestion.context && (
                <div className="rounded-xl border border-line/60 bg-ink/60 px-3.5 py-2.5 text-xs font-mono text-paper/80">
                  <span className="text-muted block text-[10px] uppercase tracking-wider mb-1">
                    Observed in your data:
                  </span>
                  {currentQuestion.context}
                </div>
              )}

              {/* Choice Chips (Quick Selection) */}
              {currentQuestion.choices && currentQuestion.choices.length > 0 && (
                <div className="space-y-3 pt-2">
                  <div className="flex items-center justify-between text-xs text-muted">
                    <span>
                      {currentQuestion.answer_type === "multi_choice"
                        ? "Quick Select relevant values:"
                        : "Quick Select primary value:"}
                    </span>
                    {currentQuestion.answer_type === "multi_choice" && (
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => handleSelectAll(currentQuestion.id, currentQuestion.choices)}
                          className="text-canonical hover:underline text-[11px]"
                        >
                          Select All
                        </button>
                        <span>·</span>
                        <button
                          type="button"
                          onClick={() => handleClear(currentQuestion.id)}
                          className="text-muted hover:text-danger text-[11px]"
                        >
                          Clear
                        </button>
                      </div>
                    )}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {currentQuestion.choices.map((choice) => {
                      const selectedValues = (values[currentQuestion.id] ?? "")
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean);
                      const isSelected = selectedValues.includes(choice);
                      const isMulti = currentQuestion.answer_type === "multi_choice";

                      return (
                        <button
                          key={choice}
                          type="button"
                          onClick={() => handleToggleChoice(currentQuestion.id, choice, isMulti)}
                          className={`group flex items-center gap-2 rounded-xl border px-3.5 py-2 text-xs font-mono font-medium transition-all ${
                            isSelected
                              ? "border-physical bg-physical/20 text-physical shadow-sm ring-1 ring-physical/40 scale-[1.02]"
                              : "border-line bg-base/60 text-paper/80 hover:border-canonical/50 hover:bg-base hover:text-paper"
                          }`}
                        >
                          <span
                            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] transition-colors ${
                              isSelected
                                ? "bg-physical text-ink font-bold"
                                : "border border-line bg-ink group-hover:border-canonical"
                            }`}
                          >
                            {isSelected ? "✓" : ""}
                          </span>
                          <span>{choice}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Free-Text Custom Input & Business Context Note (Always Available) */}
              <div className="pt-2 space-y-1.5">
                <div className="flex items-center justify-between text-xs text-muted">
                  <span className="font-medium text-paper/90">
                    {currentQuestion.choices && currentQuestion.choices.length > 0
                      ? "Custom Business Explanation or Notes (Optional / Editable):"
                      : "Your Explanation / Business Context:"}
                  </span>
                  {values[currentQuestion.id] && (
                    <button
                      type="button"
                      onClick={() => handleClear(currentQuestion.id)}
                      className="text-muted hover:text-danger text-[11px] transition-colors"
                    >
                      Clear text
                    </button>
                  )}
                </div>
                <textarea
                  value={values[currentQuestion.id] ?? ""}
                  onChange={(e) =>
                    setValues((prev) => ({ ...prev, [currentQuestion.id]: e.target.value }))
                  }
                  rows={2}
                  placeholder={
                    currentQuestion.choices && currentQuestion.choices.length > 0
                      ? "Type custom explanation or rules (e.g. 'Exclude spardha-staging and asd because they are test bots; spardha-logos is production')..."
                      : "Type your business clarification or notes for the AI KPI compiler…"
                  }
                  className="w-full resize-y rounded-xl border border-line bg-ink px-4 py-3 text-xs text-paper placeholder:text-muted focus:border-canonical focus:outline-none focus:ring-1 focus:ring-canonical/50 font-mono"
                />
              </div>
            </div>

            {/* Question Step Navigation */}
            <div className="mt-6 flex items-center justify-between border-t border-line/60 pt-4 text-xs">
              <button
                type="button"
                disabled={currentQIndex === 0}
                onClick={() => setCurrentQIndex((i) => Math.max(0, i - 1))}
                className="inline-flex items-center gap-1.5 text-muted hover:text-paper disabled:opacity-30 disabled:pointer-events-none transition-colors"
              >
                ← Previous
              </button>

              <div className="flex items-center gap-3">
                {currentQIndex < questions.length - 1 ? (
                  <button
                    type="button"
                    onClick={() => setCurrentQIndex((i) => Math.min(questions.length - 1, i + 1))}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-canonical/20 border border-canonical/40 px-4 py-2 text-xs font-semibold text-canonical hover:bg-canonical/30 transition-all cursor-pointer"
                  >
                    <span>Next Question</span>
                    <span>→</span>
                  </button>
                ) : (
                  <span className="text-[11px] font-mono text-physical">
                    ✓ All questions reviewed
                  </span>
                )}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* STEP 3: Collapsed Technical Data Quality Findings */}
      {review.findings.length > 0 && (
        <section className="border-b border-line">
          <button
            type="button"
            onClick={() => setFindingsOpen((v) => !v)}
            className="flex w-full items-center justify-between gap-3 px-6 py-3.5 text-left hover:bg-white/5 transition-colors"
          >
            <span className="flex items-center gap-2 text-xs font-semibold text-muted hover:text-paper">
              <FlagIcon />
              <span>Technical Data Quality Findings ({review.findings.length})</span>
            </span>
            <span className="flex items-center gap-2 text-xs text-muted">
              <span>{findingsOpen ? "Collapse" : "Expand details"}</span>
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
            <div className="max-h-60 overflow-y-auto space-y-2 px-6 pb-4">
              {review.findings.map((finding) => {
                const meta = SEVERITY_META[finding.severity];
                return (
                  <div
                    key={finding.id}
                    className="rounded-lg border border-line/60 bg-base/70 p-3 text-xs"
                  >
                    <div className="flex items-center gap-2">
                      <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${meta.pill}`}>
                        <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                        {meta.label}
                      </span>
                      <span className="font-mono text-paper font-semibold">
                        {finding.table}.{finding.column}
                      </span>
                    </div>
                    <p className="mt-1 text-muted text-[11px] leading-relaxed">{finding.summary}</p>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Action Footer */}
      <div className="flex flex-wrap items-center justify-between gap-4 px-6 py-4 bg-surface">
        <div className="flex items-center gap-2 text-xs text-muted">
          {hasQuestions && (
            <span className={answerCount > 0 ? "text-physical font-medium" : ""}>
              {answerCount === 0
                ? "Answers optional — click confirm to proceed"
                : `${answerCount} of ${questions.length} answered`}
            </span>
          )}
          {needsIndustry && !chosenPack && (
            <span className="text-attention font-medium">Please select an industry pack above</span>
          )}
        </div>

        <button
          type="button"
          disabled={!canContinue}
          onClick={submit}
          className="inline-flex items-center gap-2.5 rounded-xl bg-gradient-to-r from-physical to-emerald-400 px-6 py-2.5 text-xs font-bold uppercase tracking-wider text-ink shadow-lg shadow-physical/20 transition-all hover:scale-[1.02] active:scale-[0.98] disabled:scale-100 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
        >
          {submitting ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink/30 border-t-ink" />
              <span>Resuming Pipeline…</span>
            </>
          ) : (
            <>
              <span>Confirm & Build Plugin</span>
              <CheckIcon />
            </>
          )}
        </button>
      </div>
    </div>
  );
}