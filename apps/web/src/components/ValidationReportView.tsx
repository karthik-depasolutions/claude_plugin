import type { CheckStatus, ValidationReport } from "../lib/types";

const STATUS: Record<CheckStatus, string> = {
  pass: "bg-jade/15 text-jade",
  warn: "bg-amber/15 text-amber",
  fail: "bg-clay/15 text-clay",
  skipped: "bg-paper/10 text-dim",
};

export default function ValidationReportView({ report }: { report: ValidationReport }) {
  return (
    <section className="rounded-xl border border-hair bg-panel p-4 sm:p-5">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-dim">Safety gate</span>
        <span className={`rounded-full px-2 py-0.5 font-mono font-medium ${STATUS[report.overall]}`}>
          {report.overall}
        </span>
      </div>
      <ul className="mt-3 divide-y divide-hair overflow-hidden rounded-lg border border-hair">
        {report.checks.map((check) => (
          <li key={check.check} className="p-3">
            <div className="flex items-center gap-2 text-xs">
              <span className={`rounded px-1.5 py-0.5 font-mono font-medium ${STATUS[check.status]}`}>
                {check.status}
              </span>
              <span className="font-mono text-paper/85">{check.check}</span>
            </div>
            {check.skipped_reason && <p className="mt-1 text-[11px] text-dim">{check.skipped_reason}</p>}
            {check.issues.length > 0 && (
              <ul className="mt-2 space-y-1">
                {check.issues.map((issue, i) => (
                  <li key={i} className="text-[11px] text-dim">
                    <span className="font-mono text-dim/70">[{issue.location}]</span> {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
