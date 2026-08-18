import { Link } from "react-router-dom";

export default function FinalCta() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
      <div className="relative overflow-hidden rounded-3xl border border-line bg-[#0E121B] px-8 py-16 text-center sm:px-16">
        <div
          aria-hidden
          className="animate-drift pointer-events-none absolute -top-24 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-canonical/20 blur-[100px]"
        />
        <div className="relative">
          <h2 className="font-display text-3xl font-medium tracking-tight text-paper sm:text-4xl">
            Point it at your data.
          </h2>
          <p className="mx-auto mt-4 max-w-md text-[15px] text-muted">
            One dataset in, one validated plugin out — scoped to that dataset alone.
          </p>
          <Link
            to="/app"
            className="mt-8 inline-flex items-center gap-2 rounded-full bg-paper px-6 py-3 text-sm font-medium text-ink transition-transform hover:scale-[1.03]"
          >
            Start a run
            <span aria-hidden>→</span>
          </Link>
        </div>
      </div>
    </section>
  );
}
