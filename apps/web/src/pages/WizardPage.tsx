import { Link } from "react-router-dom";
import Wizard from "./Wizard";

export default function WizardPage() {
  return (
    <div className="min-h-[100dvh] bg-void font-sans text-paper antialiased">
      <header className="border-b border-hair">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-6 py-3.5">
          <Link to="/" className="flex items-baseline gap-2">
            <span className="font-display text-base font-semibold tracking-tight">Forge</span>
            <span className="font-mono text-[11px] text-dim">plugin foundry</span>
          </Link>
          <span className="flex-1" />
          <a
            href="https://github.com"
            className="font-mono text-[11px] text-dim transition-colors hover:text-paper"
          >
            source
          </a>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10 sm:py-14">
        <Wizard />
      </main>
    </div>
  );
}
