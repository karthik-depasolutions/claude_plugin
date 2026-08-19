import { useEffect, useRef, useState } from "react";
import { streamRunEvents } from "../lib/api";
import type { RunStage, RunStatus, StageEvent } from "../lib/types";

interface RunStreamState {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
}

/** Subscribes to the run's SSE feed. Bump `streamKey` to force a fresh
 * subscription after resuming a paused run (review / confirm-industry /
 * bindings) - the server closes the previous stream once a run reaches a
 * terminal or paused state, so reconnecting is how we see the resumed run's
 * events.
 *
 * The timeline is *cumulative*: on a resubscribe we keep the events we've
 * already seen and ask the server to resume from that count (`?after=N`).
 * Clearing and replaying would make a resumed run look like it started over
 * from step 1, since the orchestrator re-runs the pre-pause stages (a
 * documented constraint) - only the genuinely new post-resume events should
 * appear. */
export function useRunStream(runId: string | null, streamKey: number): RunStreamState {
  const [state, setState] = useState<RunStreamState>({ events: [], status: "pending", currentStage: null });
  const seenRef = useRef(0);

  useEffect(() => {
    if (!runId) return;
    setState((s) => ({ ...s, status: "running", currentStage: null }));

    return streamRunEvents(
      runId,
      (payload) => {
        if ("final" in payload && payload.final) {
          setState((s) => ({ ...s, status: payload.status as RunStatus }));
          return;
        }
        const event = payload as StageEvent;
        seenRef.current += 1;
        setState((s) => ({ ...s, events: [...s.events, event], currentStage: event.stage }));
      },
      seenRef.current,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, streamKey]);

  return state;
}
