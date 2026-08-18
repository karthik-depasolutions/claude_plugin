import { useState } from "react";
import { Link } from "react-router-dom";

const LINKS = [
  { href: "#mechanism", label: "How it works" },
  { href: "#features", label: "What's in it" },
  { href: "#validation", label: "Validation" },
];

export default function LandingNav() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-30 border-b border-line/80 bg-ink/80 backdrop-blur-md">
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link to="/" className="font-display text-sm font-semibold tracking-tight text-paper">
          MIS Plugin Forge
        </Link>

        <div className="hidden items-center gap-8 sm:flex">
          {LINKS.map((link) => (
            <a key={link.href} href={link.href} className="text-sm text-muted transition-colors hover:text-paper">
              {link.label}
            </a>
          ))}
          <Link
            to="/app"
            className="rounded-full border border-line px-4 py-1.5 text-sm text-paper transition-colors hover:border-canonical/60 hover:text-canonical"
          >
            Start a run
          </Link>
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label="Toggle navigation menu"
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-line text-paper sm:hidden"
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

      {open && (
        <div className="border-t border-line/80 px-6 py-4 sm:hidden">
          <div className="flex flex-col gap-4">
            {LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="text-sm text-muted hover:text-paper"
              >
                {link.label}
              </a>
            ))}
            <Link
              to="/app"
              className="mt-1 rounded-full border border-line px-4 py-2 text-center text-sm text-paper"
            >
              Start a run
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
