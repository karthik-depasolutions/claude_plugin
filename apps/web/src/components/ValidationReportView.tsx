import type { ValidationReport } from "../lib/types";
import DefinitionList from "./ui/DefinitionList";
import Panel from "./ui/Panel";
import StatusBadge, { StatusGlyph } from "./ui/StatusBadge";

export default function ValidationReportView({ report }: { report: ValidationReport }) {
  return (
    <Panel eyebrow="Safety gate" title="Eight checks" actions={<StatusBadge status={report.overall} />}>
      <DefinitionList
        termWidth="sm:w-44"
        rows={report.checks.map((check) => ({
          lead: <StatusGlyph status={check.status} />,
          term: check.check,
          detail: check.skipped_reason ? (
            <span className="text-text-faint">{check.skipped_reason}</span>
          ) : check.issues.length > 0 ? (
            <ul className="space-y-1">
              {check.issues.map((issue, i) => (
                <li key={i}>
                  <span className="font-mono text-[11px] text-text-faint">[{issue.location}]</span>{" "}
                  {issue.message}
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-text-faint">—</span>
          ),
        }))}
      />
    </Panel>
  );
}
