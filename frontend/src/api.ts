import type {
  AuditResult,
  ConfigStatus,
  EventRecord,
  ReportResponse,
  RunDetail,
  RunSummary,
} from "./types";

const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      isRecord(body) && typeof body.detail === "string"
        ? body.detail
        : `API request failed with status ${response.status}.`;
    throw new ApiError(message, response.status);
  }
  return body as T;
}

export function listRuns(): Promise<RunSummary[]> {
  return requestJson<RunSummary[]>("/api/v1/runs");
}

export function getConfig(): Promise<ConfigStatus> {
  return requestJson<ConfigStatus>("/api/v1/config");
}

export function getRun(runId: string): Promise<RunDetail> {
  return requestJson<RunDetail>(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export function getEvents(runId: string): Promise<EventRecord[]> {
  return requestJson<EventRecord[]>(
    `/api/v1/runs/${encodeURIComponent(runId)}/events`,
  );
}

export function getReport(runId: string): Promise<ReportResponse> {
  return requestJson<ReportResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/report`,
  );
}

export function startAudit(input: {
  repository_url: string;
  goal: string;
  no_ai: boolean;
}): Promise<AuditResult> {
  return requestJson<AuditResult>("/api/v1/audits", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function statusTone(status: string | null | undefined):
  | "success"
  | "partial"
  | "blocked"
  | "danger"
  | "neutral" {
  switch ((status ?? "").toUpperCase()) {
    case "REPRODUCED":
      return "success";
    case "PARTIAL":
      return "partial";
    case "BLOCKED":
      return "blocked";
    case "FAILED":
    case "UNSAFE":
      return "danger";
    default:
      return "neutral";
  }
}

export function isTerminalStage(stage: string | null | undefined): boolean {
  return (stage ?? "").toUpperCase() === "DONE";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
