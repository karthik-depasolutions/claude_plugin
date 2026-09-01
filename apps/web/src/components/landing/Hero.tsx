import { Link } from "react-router-dom";
import PipelineConsole from "./PipelineConsole";

export default function Hero() {
  return (
    <section className="relative overflow-hidden">
      <div
        aria-hidden
        className="animate-drift pointer-events-none absolute -left-40 top-0 h-[420px] w-[420px] rounded-full bg-canonical/15 blur-[120px]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-32 top-40 h-[380px] w-[380px] rounded-full bg-physical/10 blur-[120px]"
      />

      <div className="relative mx-auto grid max-w-6xl gap-16 px-6 py-20 sm:py-28 lg:grid-cols-[1.1fr_1fr] lg:items-center lg:py-32">
        <div className="animate-reveal-up">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
            data source → validated claude plugin
          </p>
          <h1 className="mt-5 font-display text-4xl font-medium leading-[1.08] tracking-tight text-paper sm:text-6xl">
            Turn one company&rsquo;s data
            <br />
            into a plugin only{" "}
            <span className="bg-gradient-to-r from-canonical to-physical bg-clip-text text-transparent">
              they
            </span>{" "}
            could have.
          </h1>
          <p className="mt-6 max-w-lg text-[15px] leading-relaxed text-muted sm:text-base">
            Point it at a CSV, a warehouse, or a live database. Get back real skills, KPIs, and MCP tools,
            bound to that dataset&rsquo;s actual columns and validated by eight checks before anything installs.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <Link
              to="/app"
              className="rounded-full bg-paper px-6 py-3 text-sm font-medium text-ink transition-transform hover:scale-[1.03]"
            >
              Start a run
            </Link>
            <a href="#mechanism" className="text-sm text-muted transition-colors hover:text-paper">
              See how it works
            </a>
          </div>
        </div>

        <div className="flex justify-center lg:justify-end">
          <PipelineConsole />
        </div>
      </div>
    </section>
  );
}
