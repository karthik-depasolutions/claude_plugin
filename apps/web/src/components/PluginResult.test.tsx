import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PluginResult from "./PluginResult";
import type { StageEvent, TokenUsage, ValidationReport } from "../lib/types";

const ev = (over: Partial<StageEvent>): StageEvent => ({
  stage: "ingest",
  message: "",
  timestamp: new Date().toISOString(),
  data: {},
  ...over,
});

const report: ValidationReport = {
  plugin_name: "retail-ecommerce-mis-plugin",
  generated_at: new Date().toISOString(),
  overall: "pass",
  checks: [
    { check: "fact_check", status: "pass", issues: [], skipped_reason: null },
    { check: "schema_model", status: "pass", issues: [], skipped_reason: null },
    { check: "cli_validate", status: "skipped", issues: [], skipped_reason: "no CLI" },
  ],
};

describe("PluginResult", () => {
  it("summarizes the build: plugin name, table & metric counts, checks passed", () => {
    const events = [
      ev({ stage: "ingest", message: "Ingested 3 table(s)", data: { tables: ["a", "b", "c"] } }),
      ev({ stage: "compile_kpis", message: "Compiled 8/8 KPI(s)" }),
      ev({ stage: "package", message: "Packaged", data: { plugin_dir: "generated/runs/x/output/retail-ecommerce-mis-plugin" } }),
    ];
    render(<PluginResult runId="run-1" events={events} report={report} />);

    expect(screen.getByText("retail-ecommerce-mis-plugin")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // tables
    expect(screen.getByText("8")).toBeInTheDocument(); // metrics
    expect(screen.getByText("2/3")).toBeInTheDocument(); // checks passed
    expect(screen.getByRole("link", { name: /download plugin/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/runs/run-1/download")
    );
  });

  it("shows the LLM token stat when usage is available", () => {
    const tokenUsage: TokenUsage = {
      input_tokens: 12300,
      output_tokens: 3100,
      total_tokens: 15400,
      calls: 9,
      by_model: {},
      by_role: {},
    };
    render(<PluginResult runId="run-1" events={[]} report={report} tokenUsage={tokenUsage} />);

    expect(screen.getByText("15k")).toBeInTheDocument(); // compact total
    expect(screen.getByText(/12k in \/ 3\.1k out/)).toBeInTheDocument();
  });

  it("shows a placeholder token stat when usage is not yet loaded", () => {
    render(<PluginResult runId="run-1" events={[]} report={report} />);
    expect(screen.getByText("LLM tokens")).toBeInTheDocument();
  });
});
