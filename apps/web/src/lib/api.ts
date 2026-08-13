import type {
  PackSummary,
  PublishGithubResponse,
  RunDetail,
  RunSummary,
  StageEvent,
  ValidationReport,
  WarehouseCredentialsResponse,
} from "./types";

// In dev, vite.config.ts proxies /runs, /packs, /health to uvicorn on :8000.
// In production this is baked in at build time (see docker-compose.yml).
const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export function listPacks(): Promise<PackSummary[]> {
  return fetch(`${BASE}/packs`).then((r) => asJson(r));
}

export function createRunFromPath(
  sourcePath: string,
  opts: { industry?: string; useLlm: boolean }
): Promise<RunSummary> {
  return fetch(`${BASE}/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ source_path: sourcePath, industry: opts.industry ?? null, use_llm: opts.useLlm }),
  }).then((r) => asJson(r));
}

export function createRunFromUpload(
  files: File[],
  opts: { industry?: string; useLlm: boolean }
): Promise<RunSummary> {
  const params = new URLSearchParams({ use_llm: String(opts.useLlm) });
  if (opts.industry) params.set("industry", opts.industry);
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return fetch(`${BASE}/runs/upload?${params}`, { method: "POST", body: form }).then((r) => asJson(r));
}

export function getRun(runId: string): Promise<RunDetail> {
  return fetch(`${BASE}/runs/${runId}`).then((r) => asJson(r));
}

export function confirmIndustry(runId: string, industry: string): Promise<RunSummary> {
  return fetch(`${BASE}/runs/${runId}/confirm-industry`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ industry }),
  }).then((r) => asJson(r));
}

export function setBindingOverrides(runId: string, overrides: Record<string, string>): Promise<RunSummary> {
  return fetch(`${BASE}/runs/${runId}/bindings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ overrides }),
  }).then((r) => asJson(r));
}

export function getReport(runId: string): Promise<ValidationReport> {
  return fetch(`${BASE}/runs/${runId}/report`).then((r) => asJson(r));
}

export function downloadUrl(runId: string): string {
  return `${BASE}/runs/${runId}/download`;
}

export function publishToGithub(
  runId: string,
  opts: { repoName?: string; owner?: string; private: boolean }
): Promise<PublishGithubResponse> {
  return fetch(`${BASE}/runs/${runId}/publish/github`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      repo_name: opts.repoName || null,
      owner: opts.owner || null,
      private: opts.private,
    }),
  }).then((r) => asJson(r));
}

/** Only returns a value for a run whose upload was loaded into the client
 * warehouse - a plain 404 (no such run ever had one) is treated as "nothing
 * to show" rather than an error, since most runs won't have this. */
export async function getWarehouseCredentials(runId: string): Promise<WarehouseCredentialsResponse | null> {
  const response = await fetch(`${BASE}/runs/${runId}/warehouse-credentials`);
  if (response.status === 404) return null;
  return asJson(response);
}

type SsePayload = StageEvent | { final: true; status: string };

export function streamRunEvents(runId: string, onMessage: (payload: SsePayload) => void): () => void {
  const source = new EventSource(`${BASE}/runs/${runId}/events`);
  source.onmessage = (event) => {
    const payload = JSON.parse(event.data) as SsePayload;
    onMessage(payload);
    if ("final" in payload && payload.final) {
      source.close();
    }
  };
  return () => source.close();
}
