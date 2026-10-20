import { useState } from "react";
import type { DataQuestion } from "../lib/types";
import Button from "./ui/Button";
import Panel from "./ui/Panel";
import Textarea from "./ui/Textarea";

interface Props {
  questions: DataQuestion[];
  onSubmit: (answers: Record<string, string>) => void;
  busy: boolean;
}

/** The pre-synthesis pause: the owner clarifies what the data means before
 *  Forge writes the knowledge pack. */
export default function ClarifyPanel({ questions, onSubmit, busy }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const filled = Object.values(answers).filter((v) => v.trim()).length;

  return (
    <Panel eyebrow="Your move" title="A few questions about your data">
      <p className="max-w-xl text-[13px] leading-relaxed text-text-muted">
        Your answers shape how Forge documents the data and writes its query cookbook. Every field is
        optional &mdash; <span className="text-text">leave them all blank and press Continue to skip</span>.
      </p>

      <ol className="mt-5 divide-y divide-border">
        {questions.map((q, i) => (
          <li key={q.id} className="py-4 first:pt-0">
            <div className="flex gap-3">
              <span className="mt-0.5 font-mono text-[11px] text-text-faint">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0 flex-1">
                <label htmlFor={q.id} className="block text-[13px] font-medium text-text">
                  {q.question}
                </label>
                {q.context && <p className="mt-1 text-[12px] text-text-faint">{q.context}</p>}
                <Textarea
                  id={q.id}
                  rows={2}
                  value={answers[q.id] ?? ""}
                  onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
                  placeholder="Type an answer, or leave blank"
                  className="mt-2"
                />
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {/* Always enabled: an empty submit is the "skip" path and must always work. */}
        <Button type="button" variant="primary" loading={busy} onClick={() => onSubmit(answers)}>
          {busy
            ? "Continuing…"
            : filled
              ? `Continue with ${filled} answer${filled === 1 ? "" : "s"}`
              : "Continue (skip all)"}
        </Button>
      </div>
    </Panel>
  );
}
