import type { ReactNode } from "react";

interface Props {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}

/** Neutral "nothing here yet" panel content. An invitation to act, not a mood. */
export default function EmptyState({ title, description, action }: Props) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-[var(--radius)] border border-dashed border-border px-5 py-8">
      <div>
        <p className="text-[13px] font-medium text-text">{title}</p>
        {description && <p className="mt-1 text-[13px] text-text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}
