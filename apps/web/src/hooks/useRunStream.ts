import { useEffect, useState } from "react";
import { streamRunEvents } from "../lib/api";
import type { RunStage, RunStatus, StageEvent } from "../lib/types";

interface RunStreamState {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
}

/** Subscribes to the run's SSE feed. Bump `streamKey` to force a fresh
 * subscription after resuming a paused run (confirm-industry / bindings) -
 * the server closes the previous stream once a run reaches a terminal or
 * paused state, so reconnecting is how we see the resumed run's events. */
export function useRunStream(runId: string | null, streamKey: number): RunStreamState {
  const [state, setState] = useState<RunStreamState>({ events: [], status: "pending", currentStage: null });

  useEffect(() => {
    if (!runId) return;
    setState((s) => ({ ...s, status: "running" }));

    return streamRunEvents(runId, (payload) => {
      if ("final" in payload && payload.final) {
        setState((s) => ({ ...s, status: payload.status as RunStatus }));
        return;
      }
      const event = payload as StageEvent;
      setState((s) => ({ ...s, events: [...s.events, event], currentStage: event.stage }));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, streamKey]);

  return state;
}
