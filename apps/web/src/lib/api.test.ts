import { afterEach, describe, expect, it, vi } from "vitest";
import { createRunFromPath, submitDataAnswers } from "./api";

function mockFetch(body: unknown) {
  const fn = vi.fn().mockResolvedValue({ ok: true, json: async () => body } as Response);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("createRunFromPath posts source_path + industry + label, never use_llm", async () => {
    const fetchFn = mockFetch({ run_id: "r1" });
    await createRunFromPath("/data/orders.csv", { industry: "retail-ecommerce", label: "Acme" });

    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/runs$/);
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string);
    expect(sent).toEqual({
      source_path: "/data/orders.csv",
      industry: "retail-ecommerce",
      label: "Acme",
    });
    expect(sent).not.toHaveProperty("use_llm");
  });

  it("submitDataAnswers posts the answers map to /answers", async () => {
    const fetchFn = mockFetch({ run_id: "r1", status: "running" });
    await submitDataAnswers("r1", { "biz:grain": "one order" });

    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toMatch(/\/runs\/r1\/answers$/);
    expect(JSON.parse(init.body as string)).toEqual({ answers: { "biz:grain": "one order" } });
  });
});
