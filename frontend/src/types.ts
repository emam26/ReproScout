export type RunStage =
  | "INTAKE"
  | "ANALYZE"
  | "PLAN"
  | "SETUP"
  | "EXECUTE"
  | "DEBUG"
  | "VERIFY"
  | "REPORT"
  | "DONE";

export type RunSummary = {
  run_id: string;
  stage: RunStage | string;
  outcome: string | null;
  context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  report_available: boolean;
};

export type EventRecord = {
  run_id: string;
  sequence: number;
  event_type: string;
  stage: string;
  timestamp: string;
  payload: unknown;
};

export type RunDetail = RunSummary & {
  event_count: number;
  report: Record<string, unknown> | null;
};

export type ReportResponse = {
  run_id: string;
  content: string;
};

export type ConfigStatus = {
  python: string;
  platform: string;
  git_available: boolean;
  docker: {
    available: boolean;
    server_version: string | null;
    error: string | null;
  };
  runs_dir: string;
  runs_dir_writable: boolean;
  llm_provider: string;
  llm_configured: boolean;
};

export type AuditResult = {
  run_id: string;
  repository: string;
  commit_sha: string;
  goal: string;
  stage: string;
  status: {
    status: string;
    verification_level: string | null;
    workflow_succeeded: boolean | null;
    clean_room_verified: boolean | null;
  };
  attempts: number;
  repairs: number;
  report_path: string;
  reproduction_package_path: string | null;
};
