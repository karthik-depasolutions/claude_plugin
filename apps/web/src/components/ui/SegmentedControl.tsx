import { cx } from "./cx";

interface Option<T extends string> {
  value: T;
  label: string;
}

interface Props<T extends string> {
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
  "aria-label": string;
}

/** A compact 2–3 way toggle. Roving tabs semantics via role="tablist". */
export default function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  "aria-label": ariaLabel,
}: Props<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="inline-flex gap-0.5 rounded-[var(--radius-sm)] border border-border bg-surface p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={cx(
              "rounded-[5px] px-3 py-1 text-xs font-medium transition-colors duration-[130ms]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
              active ? "bg-surface-2 text-text" : "text-text-muted hover:text-text",
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
