import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PipelineConsole from "./PipelineConsole";
import type { StageEvent } from "../lib/types";

const ev = (over: Partial<StageEvent>): StageEvent => ({
  stage: "ingest",
  message: "",
  timestamp: new Date().toISOString(),
  data: {},
  ...over,
});

describe("PipelineConsole", () => {
  it("shows an idle prompt before any events arrive", () => {
    render(<PipelineConsole events={[]} status="pending" currentStage={null} runId={null} />);
    expect(screen.getByText(/waiting for a build/i)).toBeInTheDocument();
  });

  it("renders operator-facing stage labels, not raw pipeline names", () => {
    render(
      <PipelineConsole
        events={[ev({ stage: "profile", message: "Profiling schema" })]}
        status="running"
        currentStage="profile"
        runId="abc123"
      />
    );
    expect(screen.getByText("Understanding the data")).toBeInTheDocument();
    expect(screen.queryByText("profile")).not.toBeInTheDocument();
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
  });

  it("renders the validation check ledger from per-check events", () => {
    const events = [
      ev({ stage: "validate", message: "fact_check: pass", data: { check: "fact_check", status: "pass" } }),
      ev({ stage: "validate", message: "schema_model: pass", data: { check: "schema_model", status: "pass" } }),
    ];
    render(<PipelineConsole events={events} status="succeeded" currentStage="validate" runId="r1" />);
    expect(screen.getByText("Every reference is real")).toBeInTheDocument();
    expect(screen.getByText("Knowledge pack is grounded")).toBeInTheDocument();
  });
});
