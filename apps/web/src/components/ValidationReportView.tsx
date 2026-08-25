import type { CheckStatus, ValidationReport } from "../lib/types";

const STATUS_STYLES: Record<CheckStatus, string> = {
  pass: "border border-physical/40 bg-physical/10 text-physical",
  warn: "border border-attention/40 bg-attention/10 text-attention",
  fail: "border border-danger/40 bg-danger/10 text-danger",
  skipped: "border border-line bg-line text-muted",
};

export default function ValidationReportView({ report }: { report: ValidationReport }) {
  return (
    <div className="animate-reveal-up space-y-3 rounded-lg border border-line bg-[#0E121B] p-4">
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted">Overall validation:</span>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${STATUS_STYLES[report.overall]}`}>
          {report.overall}
        </span>
      </div>
      <div className="divide-y divide-line rounded border border-line">
        {report.checks.map((check) => (
          <div key={check.check} className="p-3">
            <div className="flex items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-[11px] font-medium uppercase ${STATUS_STYLES[check.status]}`}>
                {check.status}
              </span>
              <span className="font-mono text-sm text-paper">{check.check}</span>
            </div>
            {check.skipped_reason && (
              <p className="mt-1 text-xs text-muted">{check.skipped_reason}</p>
            )}
            {check.issues.length > 0 && (
              <ul className="mt-2 space-y-1">
                {check.issues.map((issue, i) => (
                  <li key={i} className="text-xs text-paper/80">
                    <span className="font-mono text-muted">[{issue.location}]</span> {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
