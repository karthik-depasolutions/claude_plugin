import type { CheckStatus, ValidationReport } from "../lib/types";

const STATUS_STYLES: Record<CheckStatus, string> = {
  pass: "bg-emerald-500/20 text-emerald-400",
  warn: "bg-amber-500/20 text-amber-400",
  fail: "bg-red-500/20 text-red-400",
  skipped: "bg-slate-700 text-slate-400",
};

export default function ValidationReportView({ report }: { report: ValidationReport }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm text-slate-400">Overall:</span>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[report.overall]}`}>
          {report.overall.toUpperCase()}
        </span>
      </div>
      <div className="divide-y divide-slate-800 rounded border border-slate-800">
        {report.checks.map((check) => (
          <div key={check.check} className="p-3">
            <div className="flex items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[check.status]}`}>
                {check.status.toUpperCase()}
              </span>
              <span className="font-mono text-sm text-slate-200">{check.check}</span>
            </div>
            {check.skipped_reason && (
              <p className="mt-1 text-xs text-slate-500">{check.skipped_reason}</p>
            )}
            {check.issues.length > 0 && (
              <ul className="mt-2 space-y-1">
                {check.issues.map((issue, i) => (
                  <li key={i} className="text-xs text-slate-400">
                    <span className="font-mono text-slate-500">[{issue.location}]</span> {issue.message}
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
