import { extensionOf, humanSize } from "./fileUtils";

interface Props {
  file: File;
  onRemove: () => void;
  disabled?: boolean;
}

export default function FileRow({ file, onRemove, disabled }: Props) {
  const ext = extensionOf(file.name).replace(".", "") || "file";
  return (
    <li className="flex items-center gap-3 py-2">
      <span className="rounded-[5px] bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
        {ext}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text">{file.name}</span>
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-text-faint">
        {humanSize(file.size)}
      </span>
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Remove ${file.name}`}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[5px] text-text-faint transition-colors hover:bg-surface-2 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-40"
      >
        <svg viewBox="0 0 14 14" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" strokeLinecap="round" />
        </svg>
      </button>
    </li>
  );
}
