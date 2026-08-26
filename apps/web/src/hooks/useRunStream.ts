import { useEffect, useRef, useState } from "react";
import { getRun, streamRunEvents } from "../lib/api";
import type { RunStage, RunStatus, StageEvent } from "../lib/types";

interface RunStreamState {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
  label?: string | null;
}

/** Subscribes to the run's SSE feed. Bump `streamKey` to force a fresh
 * subscription after resuming a paused run (review / confirm-industry /
 * bindings) - the server closes the previous stream once a run reaches a
 * terminal or paused state, so reconnecting is how we see the resumed run's
 * events.
 *
 * The timeline is *cumulative*: on a resubscribe we keep the events we've
 * already seen and ask the server to resume from that count (`?after=N`). */
export function useRunStream(
  runId: string | null,
  streamKey: number
): RunStreamState & { refetch: () => void } {
  const [state, setState] = useState<RunStreamState>({ events: [], status: "pending", currentStage: null });
  const seenRef = useRef(0);
  const currentRunIdRef = useRef<string | null>(null);

  const fetchLatest = () => {
    if (!runId) return;
    getRun(runId)
      .then((detail) => {
        seenRef.current = detail.events.length;
        setState({
          events: detail.events,
          status: detail.status,
          currentStage: detail.current_stage,
          label: detail.label,
        });
      })
      .catch(() => {});
  };

  useEffect(() => {
    if (!runId) {
      setState({ events: [], status: "pending", currentStage: null });
      seenRef.current = 0;
      currentRunIdRef.current = null;
      return;
    }

    // Reset if switching runId
    if (currentRunIdRef.current !== runId) {
      currentRunIdRef.current = runId;
      seenRef.current = 0;
      setState({ events: [], status: "running", currentStage: null });
    }

    // Always fetch latest record state on runId or streamKey change
    fetchLatest();

    return streamRunEvents(
      runId,
      (payload) => {
        if ("final" in payload && payload.final) {
          setState((s) => ({ ...s, status: payload.status as RunStatus }));
          return;
        }
        const event = payload as StageEvent;
        seenRef.current += 1;
        setState((s) => {
          const exists = s.events.some(
            (e) => e.stage === event.stage && e.timestamp === event.timestamp && e.message === event.message
          );
          if (exists) return s;
          return {
            ...s,
            events: [...s.events, event],
            currentStage: event.stage,
          };
        });
      },
      seenRef.current,
    );
  }, [runId, streamKey]);

  return { ...state, refetch: fetchLatest };
}
