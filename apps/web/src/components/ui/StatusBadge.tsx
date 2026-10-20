import { cx } from "./cx";

type Tone = "ok" | "warn" | "danger" | "running" | "neutral";

interface Meta {
  tone: Tone;
  glyph: string;
  /** default visible label; callers can override */
  label: string;
}

/** Every status string the app shows — validation checks and run states —
 *  resolved to one tone + glyph so the visual language is defined once. */
const META: Record<string, Meta> = {
  pass: { tone: "ok", glyph: "✓", label: "pass" },
  succeeded: { tone: "ok", glyph: "✓", label: "succeeded" },
  warn: { tone: "warn", glyph: "!", label: "warn" },
  needs_input: { tone: "warn", glyph: "▸", label: "your move" },
  fail: { tone: "danger", glyph: "✕", label: "fail" },
  failed: { tone: "danger", glyph: "✕", label: "failed" },
  cancelled: { tone: "danger", glyph: "✕", label: "cancelled" },
  running: { tone: "running", glyph: "▸", label: "running" },
  pending: { tone: "neutral", glyph: "·", label: "pending" },
  skipped: { tone: "neutral", glyph: "○", label: "skipped" },
};

function meta(status: string): Meta {
  return META[status] ?? { tone: "neutral", glyph: "○", label: status };
}

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  running: "text-accent",
  neutral: "text-text-faint",
};

const TONE_PILL: Record<Tone, string> = {
  ok: "bg-ok/10 text-ok",
  warn: "bg-warn/10 text-warn",
  danger: "bg-danger/10 text-danger",
  running: "bg-accent/10 text-accent",
  neutral: "bg-surface-2 text-text-muted",
};

/** Just the coloured glyph — for dense ledgers and the console. */
export function StatusGlyph({ status, className }: { status: string; className?: string }) {
  const m = meta(status);
  return (
    <span aria-hidden className={cx("font-mono", TONE_TEXT[m.tone], className)}>
      {m.glyph}
    </span>
  );
}

/** Glyph + label pill. */
export default function StatusBadge({
  status,
  label,
  className,
}: {
  status: string;
  label?: string;
  className?: string;
}) {
  const m = meta(status);
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-1.5 py-0.5 font-mono text-[11px] font-medium",
        TONE_PILL[m.tone],
        className,
      )}
    >
      <span aria-hidden>{m.glyph}</span>
      {label ?? m.label}
    </span>
  );
}
