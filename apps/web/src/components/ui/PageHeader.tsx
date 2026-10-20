import type { ReactNode } from "react";

interface Props {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

/** App page header: eyebrow / title / description on the left, actions right. */
export default function PageHeader({ eyebrow, title, description, actions }: Props) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-xl">
        {eyebrow && (
          <p className="font-eyebrow text-[11px] font-medium uppercase tracking-[0.18em] text-text-faint">
            {eyebrow}
          </p>
        )}
        <h1 className="mt-2 font-display text-[26px] font-semibold leading-[1.12] tracking-[-0.02em] text-text">
          {title}
        </h1>
        {description && (
          <p className="mt-2.5 text-[13px] leading-relaxed text-text-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
