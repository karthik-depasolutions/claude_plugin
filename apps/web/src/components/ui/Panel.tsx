import type { ReactNode } from "react";
import { cx } from "./cx";

interface PanelProps {
  /** small uppercase overline above the title */
  eyebrow?: ReactNode;
  title?: ReactNode;
  /** right-aligned header content (buttons, status) */
  actions?: ReactNode;
  /** drop the body padding — for content that manages its own (e.g. a console) */
  flush?: boolean;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}

/** The single container primitive: one surface, one hairline, no nesting past
 *  one level, no shadow. */
export default function Panel({
  eyebrow,
  title,
  actions,
  flush = false,
  className,
  bodyClassName,
  children,
}: PanelProps) {
  const hasHeader = eyebrow || title || actions;
  return (
    <section
      className={cx(
        "overflow-hidden rounded-[var(--radius)] border border-border bg-surface",
        className,
      )}
    >
      {hasHeader && (
        <header className="flex items-center gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            {eyebrow && (
              <p className="font-eyebrow text-[10px] font-medium uppercase tracking-[0.16em] text-text-faint">
                {eyebrow}
              </p>
            )}
            {title && (
              <h2 className="truncate font-display text-[15px] font-semibold tracking-[-0.01em] text-text">
                {title}
              </h2>
            )}
          </div>
          {actions && <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx(!flush && "p-4 sm:p-5", bodyClassName)}>{children}</div>
    </section>
  );
}
