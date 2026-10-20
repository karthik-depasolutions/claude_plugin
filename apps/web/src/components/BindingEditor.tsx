import { useState } from "react";
import Button from "./ui/Button";
import Input from "./ui/Input";

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
      <p className="text-[13px] leading-relaxed text-text-muted">
        {unresolvedRoles.length} metric input{unresolvedRoles.length === 1 ? "" : "s"} couldn&rsquo;t be
        matched to a column. Point each one at a{" "}
        <span className="font-mono text-text">table.column</span> to rebuild, or leave it blank to skip
        those metrics.
      </p>
      <div className="space-y-2">
        {unresolvedRoles.map((role) => (
          <label key={role} className="flex items-center gap-3">
            <span className="w-40 shrink-0 font-mono text-[11px] text-text-faint">{role}</span>
            <Input
              placeholder="fact.column_name"
              value={values[role] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [role]: e.target.value }))}
            />
          </label>
        ))}
      </div>
      <Button
        type="button"
        variant="primary"
        size="sm"
        loading={submitting}
        disabled={!hasAny}
        onClick={() => onSubmit(Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim())))}
      >
        {submitting ? "Rebuilding…" : "Apply and rebuild"}
      </Button>
    </div>
  );
}
