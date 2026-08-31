import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ClarifyPanel from "./ClarifyPanel";
import type { DataQuestion } from "../lib/types";

const questions: DataQuestion[] = [
  { id: "biz:grain-orders", question: "What is one row of orders?", context: "", kind: "business" },
  { id: "biz:status", question: "Which status values count as complete?", context: "3 distinct values", kind: "business" },
];

describe("ClarifyPanel", () => {
  it("renders every question with its context", () => {
    render(<ClarifyPanel questions={questions} onSubmit={vi.fn()} busy={false} />);
    expect(screen.getByText("What is one row of orders?")).toBeInTheDocument();
    expect(screen.getByText("3 distinct values")).toBeInTheDocument();
  });

  it("submits an empty object when skipped", async () => {
    const onSubmit = vi.fn();
    render(<ClarifyPanel questions={questions} onSubmit={onSubmit} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: /skip all/i }));
    expect(onSubmit).toHaveBeenCalledWith({});
  });

  it("collects typed answers keyed by question id", async () => {
    const onSubmit = vi.fn();
    render(<ClarifyPanel questions={questions} onSubmit={onSubmit} busy={false} />);
    await userEvent.type(screen.getByLabelText("What is one row of orders?"), "one placed order");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(onSubmit).toHaveBeenCalledWith({ "biz:grain-orders": "one placed order" });
  });

  it("disables actions while busy", () => {
    render(<ClarifyPanel questions={questions} onSubmit={vi.fn()} busy />);
    expect(screen.getByRole("button", { name: /continuing/i })).toBeDisabled();
  });
});
