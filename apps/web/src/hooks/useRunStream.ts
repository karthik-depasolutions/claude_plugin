import { useEffect, useMemo, useRef, useState } from "react";
import { getRun, streamRunEvents } from "../lib/api";
import type { DataQuestion, RunStage, RunStatus, StageEvent } from "../lib/types";

const TERMINAL: RunStatus[] = ["succeeded", "failed"];

interface RunStreamState {
  events: StageEvent[];
  status: RunStatus;
  currentStage: RunStage | null;
  /** Clarification questions from the profile-stage pause, if any. */
  questions: DataQuestion[];
}

/** Subscribes to the run's SSE feed and reconciles against the persisted run
 * record. Bump `streamKey` to force a fresh subscription after resuming a
 * paused run (answers / confirm-industry / bindings). */
export function useRunStream(runId: string | null, streamKey: number): RunStreamState {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [sseStatus, setSseStatus] = useState<RunStatus>("pending");
  const [sseStage, setSseStage] = useState<RunStage | null>(null);
  // Authoritative fallback pulled from the persisted record when the SSE feed
  // is incomplete (missed `final` after an API restart, dropped stream, dev
  // double-mount). Without this the UI can sit on "running" forever and never
  // show the clarification / industry pause.
  const [pollStatus, setPollStatus] = useState<RunStatus | null>(null);
  const [pollStage, setPollStage] = useState<RunStage | null>(null);
  const [pollQuestions, setPollQuestions] = useState<DataQuestion[]>([]);
  const statusRef = useRef<RunStatus>("pending");

  const status = pollStatus ?? sseStatus;
  statusRef.current = status;

  useEffect(() => {
    if (!runId) return;
    setSseStatus("running");
    setPollStatus(null);

    const close = streamRunEvents(runId, (payload) => {
      if ("final" in payload && payload.final) {
        setSseStatus(payload.status as RunStatus);
        return;
      }
      const event = payload as StageEvent;
      setEvents((prev) => [...prev, event]);
      setSseStage(event.stage);
    });

    let stopped = false;
    const reconcile = async () => {
      if (stopped) return;
      try {
        const d = await getRun(runId);
        if (stopped) return;
        setPollStatus(d.status as RunStatus);
        if (d.current_stage) setPollStage(d.current_stage as RunStage);
        setPollQuestions(d.data_review?.questions ?? []);
      } catch {
        /* transient - try again on the next tick */
      }
    };
    const poll = setInterval(() => {
      if (!TERMINAL.includes(statusRef.current)) void reconcile();
    }, 4000);
    void reconcile();

    return () => {
      stopped = true;
      clearInterval(poll);
      close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, streamKey]);

  const eventQuestions = useMemo<DataQuestion[]>(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const q = events[i].data?.questions;
      if (Array.isArray(q) && q.length > 0) return q as DataQuestion[];
    }
    return [];
  }, [events]);

  return {
    events,
    status,
    currentStage: sseStage ?? pollStage,
    questions: eventQuestions.length > 0 ? eventQuestions : pollQuestions,
  };
}
