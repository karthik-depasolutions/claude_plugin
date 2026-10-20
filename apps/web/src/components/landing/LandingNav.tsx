import { useState } from "react";
import { Link } from "react-router-dom";
import Button from "../ui/Button";
import { cx } from "../ui/cx";

const LINKS = [
  { href: "#mechanism", label: "How it works" },
  { href: "#whats-in-it", label: "What's in it" },
  { href: "#validation", label: "Validation" },
];

export default function LandingNav() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur">
      <nav className="mx-auto flex h-14 w-full max-w-[var(--container-page)] items-center justify-between px-6">
        <Link to="/" className="flex items-baseline gap-2">
          <span className="font-display text-[15px] font-semibold tracking-[-0.02em] text-text">
            Forge
          </span>
          <span className="hidden font-mono text-[11px] text-text-faint sm:inline">plugin foundry</span>
        </Link>

        <div className="hidden items-center gap-7 md:flex">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-[13px] text-text-muted transition-colors hover:text-text"
            >
              {link.label}
            </a>
          ))}
          <Link to="/app">
            <Button variant="primary" size="sm">
              Start a run
            </Button>
          </Link>
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label="Toggle navigation menu"
          className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-sm)] border border-border text-text md:hidden"
        >
          <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.75}>
            {open ? (
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            ) : (
              <path d="M3 6h14M3 10h14M3 14h14" strokeLinecap="round" />
            )}
          </svg>
        </button>
      </nav>

      <div className={cx("border-t border-border px-6 md:hidden", open ? "block" : "hidden")}>
        <div className="flex flex-col gap-1 py-3">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              onClick={() => setOpen(false)}
              className="rounded-[var(--radius-sm)] px-2 py-2 text-[13px] text-text-muted hover:bg-surface-2 hover:text-text"
            >
              {link.label}
            </a>
          ))}
          <Link to="/app" onClick={() => setOpen(false)} className="mt-1">
            <Button variant="primary" size="sm" className="w-full">
              Start a run
            </Button>
          </Link>
        </div>
      </div>
    </header>
  );
}
