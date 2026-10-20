import type { ReactNode } from "react";
import { cx } from "./cx";

interface FieldProps {
  label: string;
  hint?: string;
  error?: string | null;
  /** Render as a plain wrapper instead of a <label> when the control has its
   *  own <label htmlFor> (e.g. a group of radios). */
  as?: "label" | "div";
  className?: string;
  children: ReactNode;
}

/** Label + optional hint + control + optional error. Wrapping the control in a
 *  <label> gives it an implicit accessible name — no id juggling needed. */
export default function Field({
  label,
  hint,
  error,
  as = "label",
  className,
  children,
}: FieldProps) {
  const Tag = as;
  return (
    <Tag className={cx("block", className)}>
      <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-[13px] font-medium text-text">{label}</span>
        {hint && <span className="text-xs text-text-faint">{hint}</span>}
      </span>
      <div className="mt-1.5">{children}</div>
      {error && (
        <p className="mt-1.5 text-xs text-danger" role="alert">
          {error}
        </p>
      )}
    </Tag>
  );
}
