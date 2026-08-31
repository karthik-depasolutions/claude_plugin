import { useState } from "react";

interface Props {
  unresolvedRoles: string[];
  onSubmit: (overrides: Record<string, string>) => void;
  submitting: boolean;
}

export default function BindingEditor({ unresolvedRoles, onSubmit, submitting }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const hasAny = Object.values(values).some((v) => v.trim());

  return (
    <div className="space-y-3">
      <p className="text-xs text-amber">
        {unresolvedRoles.length} metric input{unresolvedRoles.length === 1 ? "" : "s"} couldn&rsquo;t be
        matched to a column. Point them at one (<code className="text-paper/70">table.column</code>) to
        rebuild, or leave blank to skip those metrics.
      </p>
      <div className="space-y-2">
        {unresolvedRoles.map((role) => (
          <div key={role} className="flex items-center gap-2">
            <span className="w-40 shrink-0 font-mono text-[11px] text-dim">{role}</span>
            <input
              className="flex-1 rounded-md border border-hair bg-void px-2.5 py-1.5 text-sm placeholder:text-dim focus:border-jade focus:outline-none focus:ring-2 focus:ring-jade/25"
              placeholder="fact.column_name"
              value={values[role] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [role]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      <button
        type="button"
        disabled={submitting || !hasAny}
        onClick={() =>
          onSubmit(Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim())))
        }
        className="rounded-md bg-amber px-3.5 py-1.5 text-sm font-medium text-doc-ink transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        {submitting ? "Rebuilding…" : "Apply and rebuild"}
      </button>
    </div>
  );
}
