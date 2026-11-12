import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const preflightRun = {
  run_id: "preflight-run",
  stage: "DONE",
  outcome: "SUCCEEDED",
  context: {
    preflight_status: "PARTIAL",
    repository: "example/fixture",
    workflow: "environment-preflight",
  },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:02Z",
  report_available: true,
};

const detail = {
  ...preflightRun,
  event_count: 2,
  report: null,
};

const config = {
  python: "3.12.0",
  platform: "test-platform",
  git_available: true,
  docker: {
    available: true,
    daemon_running: true,
    context_verified: true,
    seccomp: "verified",
    server_version: "27.0",
    error: null,
  },
  runs_dir: "runs",
  runs_dir_writable: true,
  llm_provider: "mock",
  llm_configured: true,
};

const evidence = {
  run_id: "preflight-run",
  evidence: {
    commit_sha: "a".repeat(40),
    verified_environment: false,
    inspection: {
      package_requirements: [{ name: "numpy" }],
      python_constraints: [">=3.11,<3.13"],
    },
    assessment: {
      status: "PARTIAL",
      summary: "One system tool remains unverified.",
      verified_environment: false,
      requirements: [
        { kind: "PACKAGE", name: "numpy", state: "VERIFIED" },
        { kind: "SYSTEM_TOOL", name: "ffmpeg", state: "UNVERIFIED" },
      ],
      unresolved_problems: ["ffmpeg was not verified in the sandbox"],
      unverified_capabilities: ["GPU support was not requested"],
      safe_next_steps: ["Install the documented system tool, then rerun preflight."],
    },
  },
};

const events = [
  {
    run_id: "preflight-run",
    sequence: 1,
    event_type: "RUN_CREATED",
    stage: "INTAKE",
    timestamp: "2026-01-01T00:00:00Z",
    payload: {},
  },
];

describe("dashboard", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(apiFetch));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows an actionable connection error when the local API is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    await act(async () => {
      render(<App />);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "uv tool install --reinstall --editable",
    );
    expect(screen.getByText("API unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start preflight/i })).toBeDisabled();
  });

  it("keeps API availability distinct from a Docker capability error", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return Promise.resolve(jsonResponse({ status: "ok", version: "0.1.0" }));
      }
      if (url.endsWith("/api/v1/config")) {
        return Promise.resolve(jsonResponse({ detail: "Docker capability check failed" }, 500));
      }
      return Promise.resolve(jsonResponse([]));
    }));

    await act(async () => {
      render(<App />);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Docker capability check failed");
    expect(screen.getByText("Connected (v0.1.0)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start preflight/i })).toBeEnabled();
  });

  it("renders persisted preflight readiness and treats report text as text", async () => {
    await act(async () => {
      render(<App />);
    });
    expect(await screen.findByText("preflight-run")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /preflight-run/i }));
    });

    expect(await screen.findByText("One system tool remains unverified.")).toBeInTheDocument();
    expect(screen.getByText("ffmpeg was not verified in the sandbox")).toBeInTheDocument();
    expect(screen.getByText(">=3.11,<3.13")).toBeInTheDocument();

    const malicious = "<img src=x onerror=alert(1)>";
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/environment-report")) {
        return Promise.resolve(jsonResponse({ run_id: "preflight-run", content: malicious }));
      }
      return apiFetch(input);
    }));
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);
    });

    expect(await screen.findByText(malicious)).toBeInTheDocument();
    expect(document.querySelector("img")).not.toBeInTheDocument();
  });

  it("submits the default preflight route with a validated repository URL", async () => {
    const fetchMock = vi.fn(apiFetch);
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(<App />);
    });
    await screen.findByRole("button", { name: /start preflight/i });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Public GitHub URL"), {
        target: { value: "https://github.com/example/fixture" },
      });
    });

    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: /start preflight/i }).closest("form")!);
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/preflights",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("rejects an invalid repository before contacting the API", async () => {
    const fetchMock = vi.fn(apiFetch);
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(<App />);
    });
    await screen.findByRole("button", { name: /start preflight/i });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Public GitHub URL"), {
        target: { value: "https://gitlab.com/example/fixture" },
      });
    });
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: /start preflight/i }).closest("form")!);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a public HTTPS GitHub repository URL.",
    );
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/v1/preflights",
      expect.anything(),
    );
  });

  it("downloads bounded evidence through a browser blob", async () => {
    const createObjectURL = vi.fn(() => "blob:fixture");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await act(async () => {
      render(<App />);
    });
    await screen.findByText("preflight-run");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /preflight-run/i }));
    });
    await screen.findByText("One system tool remains unverified.");

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "Download" })[1]);
    });

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fixture");
    expect(click).toHaveBeenCalledTimes(1);
  });
});

function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  if (url.endsWith("/api/v1/health")) {
    return Promise.resolve(jsonResponse({ status: "ok", version: "0.1.0" }));
  }
  if (url.endsWith("/api/v1/config")) return Promise.resolve(jsonResponse(config));
  if (url.endsWith("/api/v1/runs")) return Promise.resolve(jsonResponse([preflightRun]));
  if (url.endsWith("/api/v1/preflights") && init?.method === "POST") {
    return Promise.resolve(jsonResponse({
      run_id: "preflight-run",
      repository: "example/fixture",
      commit_sha: "a".repeat(40),
      status: "PARTIAL",
      verified_environment: false,
      artifact_paths: {},
    }, 201));
  }
  if (url.endsWith("/events")) return Promise.resolve(jsonResponse(events));
  if (url.endsWith("/environment-evidence")) return Promise.resolve(jsonResponse(evidence));
  if (url.endsWith("/environment-report")) {
    return Promise.resolve(jsonResponse({ run_id: "preflight-run", content: "safe report" }));
  }
  return Promise.resolve(jsonResponse(detail));
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
