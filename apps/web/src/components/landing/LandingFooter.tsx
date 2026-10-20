import { Link } from "react-router-dom";

export default function LandingFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex w-full max-w-[var(--container-page)] flex-col items-start justify-between gap-4 px-6 py-8 sm:flex-row sm:items-center">
        <div>
          <p className="font-display text-[13px] font-semibold tracking-[-0.01em] text-text">
            MIS Plugin Forge
          </p>
          <p className="mt-1 font-mono text-[11px] text-text-faint">
            One engine. Every customer&rsquo;s own data. Nothing shared.
          </p>
        </div>
        <Link
          to="/app"
          className="font-mono text-[11px] text-text-faint transition-colors hover:text-text"
        >
          Start a run →
        </Link>
      </div>
    </footer>
  );
}
