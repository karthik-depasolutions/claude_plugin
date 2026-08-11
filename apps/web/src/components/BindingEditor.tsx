import { useState } from "react";

interface Props {
  unresolvedRoles: string[];
  onSubmit: (overrides: Record<string, string>) => void;
  submitting: boolean;
}

export default function BindingEditor({ unresolvedRoles, onSubmit, submitting }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});

  return (
    <div className="space-y-3 rounded border border-amber-800/50 bg-amber-950/20 p-4">
      <p className="text-sm text-amber-300">
        These canonical roles couldn't be matched automatically. Provide the physical column
        (<code>table.column</code>) for any you'd like to force, then re-run.
      </p>
      <div className="space-y-2">
        {unresolvedRoles.map((role) => (
          <div key={role} className="flex items-center gap-2">
            <span className="w-40 shrink-0 font-mono text-xs text-slate-400">{role}</span>
            <input
              className="flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
              placeholder="fact.column_name"
              value={values[role] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [role]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      <button
        type="button"
        disabled={submitting || Object.values(values).every((v) => !v.trim())}
        onClick={() => {
          const overrides = Object.fromEntries(
            Object.entries(values).filter(([, v]) => v.trim().length > 0)
          );
          onSubmit(overrides);
        }}
        className="rounded bg-amber-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
      >
        {submitting ? "Re-running…" : "Apply overrides and re-run"}
      </button>
    </div>
  );
}
