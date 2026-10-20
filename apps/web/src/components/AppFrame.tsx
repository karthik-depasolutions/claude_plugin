import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** The shared app shell: one compact top bar, one dark ground, one working
 *  column. Used by every route under /app. */
export default function AppFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[100dvh] flex-col bg-bg text-text">
      <header className="sticky top-0 z-30 border-b border-border bg-bg/85 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-[var(--container-app)] items-center gap-3 px-6">
          <Link to="/" className="group flex items-baseline gap-2">
            <span className="font-display text-[15px] font-semibold tracking-[-0.02em] text-text">
              Forge
            </span>
            <span className="hidden font-mono text-[11px] text-text-faint transition-colors group-hover:text-text-muted sm:inline">
              plugin foundry
            </span>
          </Link>
          <span className="flex-1" />
          <a
            href="https://github.com"
            className="font-mono text-[11px] text-text-faint transition-colors hover:text-text"
          >
            source
          </a>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[var(--container-app)] flex-1 px-6 py-10 sm:py-12">
        {children}
      </main>
    </div>
  );
}
