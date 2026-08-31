export type RunStatus = "pending" | "running" | "needs_input" | "succeeded" | "failed" | "cancelled";

export type RunStage =
  | "ingest"
  | "profile"
  | "classify"
  | "bind"
  | "compile_kpis"
  | "generate"
  | "validate"
  | "package"
  | "publish";

export const STAGE_ORDER: RunStage[] = [
  "ingest",
  "profile",
  "classify",
  "bind",
  "compile_kpis",
  "generate",
  "package",
  "validate",
];

export interface StageEvent {
  stage: RunStage;
  message: string;
  timestamp: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  current_stage: RunStage | null;
  error: string | null;
}

export interface DataQuestion {
  id: string;
  question: string;
  context: string;
  kind: "quality" | "business";
}

export interface QualityFinding {
  id: string;
  code: string;
  severity: string;
  table: string;
  column: string;
  summary: string;
}

export interface DataReview {
  generated_at: string;
  findings: QualityFinding[];
  questions: DataQuestion[];
  sampled_tables: string[];
}

export interface RunDetail extends RunSummary {
  source_path: string;
  industry_override: string | null;
  output_dir: string;
  label: string | null;
  events: StageEvent[];
  data_review: DataReview | null;
  data_answers: Record<string, string> | null;
  created_at: string;
  updated_at: string;
}

export interface RankedMatch {
  pack_slug: string;
  confidence: number;
  matched_signals: string[];
}

export interface PackSummary {
  slug: string;
  name: string;
  version: string;
  description: string;
  kpi_count: number;
}

export type CheckStatus = "pass" | "warn" | "fail" | "skipped";

export interface ValidationIssue {
  severity: string;
  location: string;
  message: string;
  suggestion: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  details: Record<string, any>;
}

export interface ValidationCheckResult {
  check: string;
  status: CheckStatus;
  issues: ValidationIssue[];
  skipped_reason: string | null;
}

export interface ValidationReport {
  plugin_name: string;
  generated_at: string;
  checks: ValidationCheckResult[];
  overall: CheckStatus;
}

export interface PublishGithubResponse {
  repo_full_name: string;
  html_url: string;
  plugin_name: string;
  marketplace_add_command: string;
  install_command: string;
}

export interface WarehouseCredentialsResponse {
  connection_string: string;
  env_var_name: string;
}
