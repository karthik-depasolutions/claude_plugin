import { Link } from "react-router-dom";
import Button from "../ui/Button";

export default function FinalCta() {
  return (
    <section className="border-t border-border">
      <div className="mx-auto flex w-full max-w-[var(--container-page)] flex-col items-start gap-4 px-6 py-12 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-[22px] font-semibold tracking-[-0.02em] text-text sm:text-[26px]">
            Point it at your data.
          </h2>
          <p className="mt-1.5 text-[13px] text-text-muted">
            One dataset in, one validated plugin out &mdash; scoped to that dataset alone.
          </p>
        </div>
        <Link to="/app" className="shrink-0">
          <Button variant="primary" size="md">
            Start a run
          </Button>
        </Link>
      </div>
    </section>
  );
}
