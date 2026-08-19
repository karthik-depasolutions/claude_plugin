import { Link } from "react-router-dom";

export default function LandingFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-4 px-6 py-10 sm:flex-row sm:items-center">
        <div>
          <p className="font-display text-sm font-semibold text-paper">Data2plugin</p>
          <p className="mt-1 text-xs text-muted">One engine. Every customer&rsquo;s own data. Nothing shared.</p>
        </div>
        <Link to="/app" className="text-xs text-muted transition-colors hover:text-paper">
          Start a run →
        </Link>
      </div>
    </footer>
  );
}
