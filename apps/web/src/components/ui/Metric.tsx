import type { ReactNode } from "react";
import { cx } from "./cx";

interface Props {
  value: ReactNode;
  label: ReactNode;
  className?: string;
}

/** A single number + its label. Tabular figures so a row of them aligns. */
export default function Metric({ value, label, className }: Props) {
  return (
    <div className={cx("min-w-0", className)}>
      <div className="font-display text-[22px] font-semibold tabular-nums leading-none tracking-[-0.01em] text-text">
        {value}
      </div>
      <div className="mt-1.5 text-[11px] uppercase leading-snug tracking-[0.08em] text-text-faint">
        {label}
      </div>
    </div>
  );
}
