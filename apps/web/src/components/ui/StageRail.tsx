import { STAGE_ORDER, type RunStage, type RunStatus } from "../../lib/types";
import { cx } from "./cx";

/** Short mono keys for the rail — the pipeline's own vocabulary, trimmed. */
const KEY: Record<RunStage, string> = {
  ingest: "ingest",
  profile: "profile",
  classify: "classify",
  bind: "bind",
  compile_kpis: "compile",
  generate: "generate",
  package: "package",
  validate: "validate",
  publish: "publish",
};

type NodeState = "done" | "current" | "pending" | "failed" | "input";

function Pip({ state, spec }: { state: NodeState; spec: boolean }) {
  const wrap = "flex h-3 w-3 shrink-0 items-center justify-center";
  if (state === "current") {
    return (
      <span className={wrap}>
        <span className="animate-pulse-ring h-[9px] w-[9px] rounded-full bg-accent" />
      </span>
    );
  }
  if (state === "done") {
    return (
      <span className={wrap}>
        <span className="h-[7px] w-[7px] rounded-full bg-ok" />
      </span>
    );
  }
  if (state === "failed" || state === "input") {
    return (
      <span className={wrap}>
        <span className={cx("h-[9px] w-[9px] rounded-full", state === "failed" ? "bg-danger" : "bg-warn")} />
      </span>
    );
  }
  return (
    <span className={wrap}>
      <span className={cx("h-[6px] w-[6px] rounded-full", spec ? "bg-text-faint" : "border border-border")} />
    </span>
  );
}

interface Props {
  variant: "spec" | "live";
  currentStage?: RunStage | null;
  status?: RunStatus;
  className?: string;
}

/** The signature element. A typographic track of the real pipeline stages.
 *  `spec` — static, the product's own definition (landing hero).
 *  `live` — driven by the run, the primary progress indicator. */
export default function StageRail({ variant, currentStage, status, className }: Props) {
  const spec = variant === "spec";
  const reached = !spec && currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;
  const last = STAGE_ORDER.length - 1;

  function stateFor(i: number): NodeState {
    if (spec) return "pending";
    if (i < reached) return "done";
    if (i === reached) {
      if (status === "succeeded") return "done";
      if (status === "failed" || status === "cancelled") return "failed";
      if (status === "needs_input") return "input";
      return "current";
    }
    return "pending";
  }

  const LABEL_COLOR: Record<NodeState, string> = {
    done: "text-text-muted",
    current: "text-text",
    failed: "text-danger",
    input: "text-warn",
    pending: spec ? "text-text-muted" : "text-text-faint",
  };

  return (
    <div className={cx("no-scrollbar overflow-x-auto", className)}>
      <ol className="mx-auto flex min-w-max max-w-[760px] sm:min-w-0">
        {STAGE_ORDER.map((stage, i) => {
          const s = stateFor(i);
          const litLeft = i !== 0 && (spec || i <= reached);
          const litRight = i !== last && (spec || i < reached);
          return (
            <li key={stage} className="flex flex-1 flex-col items-center gap-2 px-1">
              <div className="flex w-full items-center">
                <span
                  aria-hidden
                  className={cx(
                    "h-[1.5px] flex-1",
                    i === 0 && "opacity-0",
                    litLeft ? "bg-border-strong" : "bg-border",
                  )}
                />
                <Pip state={s} spec={spec} />
                <span
                  aria-hidden
                  className={cx(
                    "h-[1.5px] flex-1",
                    i === last && "opacity-0",
                    litRight ? "bg-border-strong" : "bg-border",
                  )}
                />
              </div>
              <span
                className={cx(
                  "font-mono text-[10.5px] uppercase tracking-[0.05em]",
                  LABEL_COLOR[s],
                )}
              >
                {KEY[stage]}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
