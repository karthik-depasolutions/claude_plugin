import type { ReactNode } from "react";

interface Props {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}

/** A failure surface: what happened and what to do, in the interface's voice.
 *  A left rule in the danger tone, no shouting fill. */
export default function ErrorState({ title, description, action }: Props) {
  return (
    <div
      role="alert"
      className="rounded-[var(--radius)] border border-border border-l-2 border-l-danger bg-surface px-5 py-4"
    >
      <p className="font-eyebrow text-[10px] font-medium uppercase tracking-[0.16em] text-danger">
        Stopped
      </p>
      <p className="mt-1.5 font-display text-[15px] font-semibold text-text">{title}</p>
      {description && <p className="mt-1.5 text-[13px] leading-relaxed text-text-muted">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
