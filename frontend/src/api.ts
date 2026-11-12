import type {
  AuditResult,
  ConfigStatus,
  EnvironmentEvidence,
  EventRecord,
  PreflightResult,
  ReportResponse,
  RunDetail,
  RunSummary,
} from "./types";
import {
  API_HEALTH_PATH,
  API_PREFIX,
  LOCAL_API_SETUP_GUIDANCE,
} from "./apiConfig";

const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ApiUnavailableError extends ApiError {
  constructor(status = 0) {
    super(LOCAL_API_SETUP_GUIDANCE, status);
    this.name = "ApiUnavailableError";
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (reason) {
    if (path.startsWith(API_PREFIX)) {
      throw new ApiUnavailableError();
    }
    throw reason;
  }

  const text = await response.text();
  const body = parseJson(text);
  if (!response.ok) {
    if (isRecord(body) && typeof body.detail === "string" && body.detail) {
      throw new ApiError(body.detail, response.status);
    }
    if ([502, 503, 504].includes(response.status) && path.startsWith(API_PREFIX)) {
      throw new ApiUnavailableError(response.status);
    }
    throw new ApiError(`API request failed with status ${response.status}.`, response.status);
  }
  if (body === undefined) {
    throw new ApiError(
      "The local API returned an invalid response.",
      response.status,
    );
  }
  return body as T;
}

export function getHealth(): Promise<{ status: string; version: string }> {
  return requestJson<{ status: string; version: string }>(API_HEALTH_PATH);
}

export function listRuns(): Promise<RunSummary[]> {
  return requestJson<RunSummary[]>(`${API_PREFIX}/runs`);
}

export function getConfig(): Promise<ConfigStatus> {
  return requestJson<ConfigStatus>(`${API_PREFIX}/config`);
}

export function getRun(runId: string): Promise<RunDetail> {
  return requestJson<RunDetail>(`${API_PREFIX}/runs/${encodeURIComponent(runId)}`);
}

export function getEvents(runId: string): Promise<EventRecord[]> {
  return requestJson<EventRecord[]>(
    `${API_PREFIX}/runs/${encodeURIComponent(runId)}/events`,
  );
}

export function getReport(runId: string): Promise<ReportResponse> {
  return requestJson<ReportResponse>(
    `${API_PREFIX}/runs/${encodeURIComponent(runId)}/report`,
  );
}

export function getEnvironmentReport(runId: string): Promise<ReportResponse> {
  return requestJson<ReportResponse>(
    `${API_PREFIX}/runs/${encodeURIComponent(runId)}/environment-report`,
  );
}

export function getAgentHandoff(runId: string): Promise<ReportResponse> {
  return requestJson<ReportResponse>(
    `${API_PREFIX}/runs/${encodeURIComponent(runId)}/agent-handoff`,
  );
}

export function getEnvironmentEvidence(
  runId: string,
): Promise<EnvironmentEvidence> {
  return requestJson<EnvironmentEvidence>(
    `${API_PREFIX}/runs/${encodeURIComponent(runId)}/environment-evidence`,
  );
}

export function startAudit(input: {
  repository_url: string;
  goal: string;
  no_ai: boolean;
}): Promise<AuditResult> {
  return requestJson<AuditResult>(`${API_PREFIX}/audits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function startPreflight(input: {
  repository_url: string;
  no_ai: boolean;
}): Promise<PreflightResult> {
  return requestJson<PreflightResult>(`${API_PREFIX}/preflights`, {
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
    case "INCOMPLETE":
    case "UNVERIFIED":
      return "partial";
    case "BLOCKED":
      return "blocked";
    case "FAILED":
    case "UNSAFE":
    case "INCOMPATIBLE":
    case "INSTALLATION_FAILED":
    case "MISSING_PREREQUISITES":
      return "danger";
    case "READY":
      return "success";
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

function parseJson(text: string): unknown | undefined {
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}
