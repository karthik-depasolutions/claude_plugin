import type { ReactNode } from "react";
import { cx } from "./cx";

interface SectionProps {
  id?: string;
  eyebrow?: string;
  title?: ReactNode;
  /** short supporting line under the title */
  lede?: ReactNode;
  /** pull the top padding in — for sections that butt against the one above */
  tight?: boolean;
  /** a hairline top rule marking a hard section change */
  divided?: boolean;
  className?: string;
  children: ReactNode;
}

/** A landing section: consistent vertical rhythm, the page container, and an
 *  optional eyebrow / heading / lede block. */
export default function Section({
  id,
  eyebrow,
  title,
  lede,
  tight = false,
  divided = false,
  className,
  children,
}: SectionProps) {
  const hasHeading = Boolean(eyebrow || title || lede);
  return (
    <section
      id={id}
      className={cx(
        divided && "border-t border-border",
        tight ? "py-10 sm:py-14" : "py-12 sm:py-16",
        className,
      )}
    >
      <div className="mx-auto w-full max-w-[var(--container-page)] px-6">
        {hasHeading && (
          <div className="max-w-2xl">
            {eyebrow && (
              <p className="font-eyebrow text-[11px] font-medium uppercase tracking-[0.18em] text-text-faint">
                {eyebrow}
              </p>
            )}
            {title && (
              <h2 className="mt-3 font-display text-[28px] font-semibold leading-[1.1] tracking-[-0.02em] text-text sm:text-[34px]">
                {title}
              </h2>
            )}
            {lede && <p className="mt-3 text-[15px] leading-relaxed text-text-muted">{lede}</p>}
          </div>
        )}
        <div className={cx(hasHeading && "mt-10")}>{children}</div>
      </div>
    </section>
  );
}
