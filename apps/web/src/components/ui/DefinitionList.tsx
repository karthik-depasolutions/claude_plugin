import type { ReactNode } from "react";
import { cx } from "./cx";

export interface DefRow {
  term: ReactNode;
  detail: ReactNode;
  /** optional leading element (status glyph, index) */
  lead?: ReactNode;
}

interface Props {
  rows: DefRow[];
  /** stack term over detail instead of side-by-side */
  stacked?: boolean;
  /** term typeface — mono for identifiers/checks, sans for prose terms */
  termFont?: "mono" | "sans";
  /** width of the term column on sm+ when not stacked */
  termWidth?: string;
  className?: string;
}

/** Hairline-separated term / detail rows. Replaces bordered card grids for
 *  feature lists, validation ledgers, and run detail. */
export default function DefinitionList({
  rows,
  stacked = false,
  termFont = "mono",
  termWidth = "sm:w-52",
  className,
}: Props) {
  return (
    <dl className={cx("divide-y divide-border", className)}>
      {rows.map((row, i) => (
        <div
          key={i}
          className={cx(
            "flex py-3.5 first:pt-0 last:pb-0",
            stacked
              ? "flex-col gap-1.5"
              : "flex-col gap-y-1 sm:flex-row sm:items-baseline sm:gap-x-4 sm:gap-y-0",
          )}
        >
          <dt
            className={cx(
              "flex items-center gap-2 text-text",
              termFont === "mono" ? "font-mono text-[12px]" : "text-[13px] font-medium",
              !stacked && cx(termWidth, "sm:shrink-0"),
            )}
          >
            {row.lead}
            {row.term}
          </dt>
          <dd className="min-w-0 flex-1 text-[13px] leading-relaxed text-text-muted">{row.detail}</dd>
        </div>
      ))}
    </dl>
  );
}
