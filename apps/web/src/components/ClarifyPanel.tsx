import { useState } from "react";
import type { DataQuestion } from "../lib/types";

interface Props {
  questions: DataQuestion[];
  onSubmit: (answers: Record<string, string>) => void;
  busy: boolean;
}

/** The pre-synthesis pause: the owner clarifies what the data means before
 * Forge writes the knowledge pack. Rendered on the warm "paper" surface
 * because this is where the person acts, not where the machine reports. */
export default function ClarifyPanel({ questions, onSubmit, busy }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const filled = Object.values(answers).filter((v) => v.trim()).length;

  return (
    <section className="rounded-xl bg-doc p-6 text-doc-ink shadow-lg shadow-black/20 sm:p-8">
      <div className="max-w-xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-amber">Your move</p>
        <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-tight">
          A few questions about your data
        </h2>
        <p className="mt-2 text-sm text-doc-ink/70">
          Your answers shape how Forge documents the data and writes its query cookbook. Answer what
          you know; skip anything you&rsquo;re unsure about.
        </p>
      </div>

      <ol className="mt-6 space-y-5">
        {questions.map((q, i) => (
          <li key={q.id} className="border-t border-doc-hair pt-5 first:border-t-0 first:pt-0">
            <div className="flex gap-3">
              <span className="mt-0.5 font-mono text-xs text-doc-ink/40">{String(i + 1).padStart(2, "0")}</span>
              <div className="min-w-0 flex-1">
                <label htmlFor={q.id} className="block text-sm font-medium">
                  {q.question}
                </label>
                {q.context && <p className="mt-1 text-xs text-doc-ink/55">{q.context}</p>}
                <textarea
                  id={q.id}
                  rows={2}
                  value={answers[q.id] ?? ""}
                  onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
                  placeholder="Type an answer, or leave blank"
                  className="mt-2 w-full resize-y rounded-md border border-doc-hair bg-white/60 px-3 py-2 text-sm text-doc-ink placeholder:text-doc-ink/35 focus:border-amber focus:outline-none focus:ring-2 focus:ring-amber/30"
                />
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-7 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => onSubmit(answers)}
          className="rounded-md bg-amber px-4 py-2 text-sm font-medium text-doc-ink transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Continuing…" : `Continue${filled ? ` with ${filled} answer${filled === 1 ? "" : "s"}` : ""}`}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onSubmit({})}
          className="text-sm text-doc-ink/60 underline-offset-4 hover:text-doc-ink hover:underline disabled:opacity-50"
        >
          Skip all
        </button>
      </div>
    </section>
  );
}
