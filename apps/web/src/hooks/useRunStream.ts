import { useEffect, useMemo, useState } from "react";
import { streamRunEvents } from "../lib/api";
import type { DataQuestion, RunStage, RunStatus, StageEvent } from "../lib/types";

interface RunStreamState {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
  /** Clarification questions from the profile-stage pause, if any. */
  questions: DataQuestion[];
}

/** Subscribes to the run's SSE feed. Bump `streamKey` to force a fresh
 * subscription after resuming a paused run (answers / confirm-industry /
 * bindings) - the server closes the previous stream once a run reaches a
 * terminal or paused state, so reconnecting is how we see the resumed run. */
export function useRunStream(runId: string | null, streamKey: number): RunStreamState {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [status, setStatus] = useState<RunStatus>("pending");
  const [currentStage, setCurrentStage] = useState<RunStage | null>(null);

  useEffect(() => {
    if (!runId) return;
    setStatus("running");

    return streamRunEvents(runId, (payload) => {
      if ("final" in payload && payload.final) {
        setStatus(payload.status as RunStatus);
        return;
      }
      const event = payload as StageEvent;
      setEvents((prev) => [...prev, event]);
      setCurrentStage(event.stage);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, streamKey]);

  const questions = useMemo<DataQuestion[]>(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const q = events[i].data?.questions;
      if (Array.isArray(q) && q.length > 0) return q as DataQuestion[];
    }
    return [];
  }, [events]);

  return { events, status, currentStage, questions };
}
