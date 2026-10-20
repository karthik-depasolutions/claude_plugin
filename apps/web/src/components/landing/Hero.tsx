import { Link } from "react-router-dom";
import Button from "../ui/Button";
import StageRail from "../ui/StageRail";

export default function Hero() {
  return (
    <section className="border-b border-border">
      <div className="mx-auto w-full max-w-[var(--container-page)] px-6 pb-12 pt-14 sm:pb-16 sm:pt-20">
        <p className="font-eyebrow text-[11px] font-medium uppercase tracking-[0.18em] text-text-faint">
          Data source → validated Claude plugin
        </p>

        <h1 className="mt-4 max-w-3xl font-display text-[34px] font-semibold leading-[1.06] tracking-[-0.03em] text-text sm:text-[52px]">
          Turn one company&rsquo;s data into a plugin only they could have.
        </h1>

        <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-text-muted">
          Point Forge at a CSV, a directory of tables, or a live read-only database. It profiles the
          schema, binds industry KPIs to your real columns, and ships an installable Claude Code plugin
          &mdash; skills, metrics, and MCP tools, all scoped to that dataset alone.
        </p>

        <div className="mt-7 flex flex-wrap items-center gap-4">
          <Link to="/app">
            <Button variant="primary" size="md">
              Start a run
            </Button>
          </Link>
          <a
            href="#mechanism"
            className="text-[13px] text-text-muted underline-offset-4 transition-colors hover:text-text hover:underline"
          >
            See how it works
          </a>
        </div>

        <div className="mt-12 rounded-[var(--radius)] border border-border bg-surface p-5 sm:p-6">
          <p className="font-eyebrow text-[10px] font-medium uppercase tracking-[0.16em] text-text-faint">
            The pipeline
          </p>
          <div className="mt-4">
            <StageRail variant="spec" />
          </div>
          <p className="mt-5 border-t border-border pt-4 text-[12px] leading-relaxed text-text-muted">
            Eight deterministic stages, one validation gate &mdash; the same path whether the source is
            a three-column CSV or a forty-table warehouse.
          </p>
        </div>
      </div>
    </section>
  );
}
