import type {
  CurrentUser,
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

// `credentials: "include"` on every call - the login session travels as an
// HttpOnly cookie (see forge_api.security), and the dev server's API origin
// (:8000) differs from the web origin (:5173), so the browser won't attach
// it unless asked.
function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE}${path}`, { ...init, credentials: "include" });
}

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export function login(email: string, password: string): Promise<CurrentUser> {
  return apiFetch(`/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  }).then((r) => asJson(r));
}

export async function logout(): Promise<void> {
  await apiFetch(`/auth/logout`, { method: "POST" });
}

export function getCurrentUser(): Promise<CurrentUser> {
  return apiFetch(`/auth/me`).then((r) => asJson(r));
}

export function listPacks(): Promise<PackSummary[]> {
  return apiFetch(`/packs`).then((r) => asJson(r));
}

export function createRunFromPath(
  sourcePath: string,
  opts: { industry?: string; useLlm: boolean; useAgent?: boolean; label?: string }
): Promise<RunSummary> {
  return apiFetch(`/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      source_path: sourcePath,
      industry: opts.industry ?? null,
      use_llm: opts.useLlm,
      use_agent: opts.useAgent ?? opts.useLlm,
      label: opts.label ?? null,
    }),
  }).then((r) => asJson(r));
}

export function createRunFromUpload(
  files: File[],
  opts: { industry?: string; useLlm: boolean; useAgent?: boolean; label?: string }
): Promise<RunSummary> {
  const params = new URLSearchParams({
    use_llm: String(opts.useLlm),
    use_agent: String(opts.useAgent ?? opts.useLlm),
  });
  if (opts.industry) params.set("industry", opts.industry);
  if (opts.label) params.set("label", opts.label);
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return apiFetch(`/runs/upload?${params}`, { method: "POST", body: form }).then((r) => asJson(r));
}

export function getRun(runId: string): Promise<RunDetail> {
  return apiFetch(`/runs/${runId}`).then((r) => asJson(r));
}

export function confirmIndustry(runId: string, industry: string): Promise<RunSummary> {
  return apiFetch(`/runs/${runId}/confirm-industry`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ industry }),
  }).then((r) => asJson(r));
}

export function submitReview(
  runId: string,
  body: { industry?: string; answers: Record<string, string> }
): Promise<RunSummary> {
  return apiFetch(`/runs/${runId}/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      industry: body.industry ?? null,
      answers: body.answers,
    }),
  }).then((r) => asJson(r));
}

export function setBindingOverrides(runId: string, overrides: Record<string, string>): Promise<RunSummary> {
  return apiFetch(`/runs/${runId}/bindings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ overrides }),
  }).then((r) => asJson(r));
}

export function confirmBindings(runId: string, confirmations: Record<string, string>): Promise<RunSummary> {
  return apiFetch(`/runs/${runId}/confirm-bindings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ confirmations }),
  }).then((r) => asJson(r));
}

export function getReport(runId: string): Promise<ValidationReport> {
  return apiFetch(`/runs/${runId}/report`).then((r) => asJson(r));
}

export function downloadUrl(runId: string): string {
  return `${BASE}/runs/${runId}/download`;
}

export function publishToGithub(
  runId: string,
  opts: { repoName?: string; owner?: string; private: boolean }
): Promise<PublishGithubResponse> {
  return apiFetch(`/runs/${runId}/publish/github`, {
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
  const response = await apiFetch(`/runs/${runId}/warehouse-credentials`);
  if (response.status === 404) return null;
  return asJson(response);
}

type SsePayload = StageEvent | { final: true; status: string };

export function streamRunEvents(
  runId: string,
  onMessage: (payload: SsePayload) => void,
  after = 0,
): () => void {
  // EventSource has no fetch-style `init` - `withCredentials` is its own
  // equivalent of `credentials: "include"` for the session cookie.
  const source = new EventSource(`${BASE}/runs/${runId}/events?after=${after}`, { withCredentials: true });
  source.onmessage = (event) => {
    const payload = JSON.parse(event.data) as SsePayload;
    onMessage(payload);
    if ("final" in payload && payload.final) {
      source.close();
    }
  };
  return () => source.close();
}
