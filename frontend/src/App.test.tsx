import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const runs = [
  {
    run_id: "fixture-run",
    stage: "DONE",
    outcome: "SUCCEEDED",
    context: { repository: "example/fixture" },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:02Z",
    report_available: true,
  },
];

const detail = {
  ...runs[0],
  event_count: 2,
  report: {
    final_status: "REPRODUCED",
    failures: [],
    diagnoses: [],
    repairs: [],
    blockers: [],
  },
};

const config = {
  python: "3.12.0",
  platform: "test-platform",
  git_available: true,
  docker: { available: true, server_version: "27.0", error: null },
  runs_dir: "runs",
  runs_dir_writable: true,
  llm_provider: "mock",
  llm_configured: true,
};

describe("dashboard", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/config")) return Promise.resolve(jsonResponse(config));
        if (url.endsWith("/api/v1/runs")) return Promise.resolve(jsonResponse(runs));
        if (url.endsWith("/events")) return Promise.resolve(jsonResponse([]));
        if (url.endsWith("/report")) return Promise.resolve(jsonResponse({ run_id: "fixture-run", content: "safe report" }));
        return Promise.resolve(jsonResponse(detail));
      }),
    );
  });

  it("shows empty state when the API has no runs", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/config")) return Promise.resolve(jsonResponse(config));
      return Promise.resolve(jsonResponse([]));
    }));
    render(<App />);
    expect(await screen.findByText("No audits yet. Start with a public repository.")).toBeInTheDocument();
    expect(await screen.findByText("Runtime status")).toBeInTheDocument();
  });

  it("loads a reproduced run and renders untrusted report text as text", async () => {
    render(<App />);
    expect(await screen.findByText("fixture-run")).toBeInTheDocument();
    screen.getByRole("button", { name: /fixture-run/i }).click();
    await waitFor(() =>
      expect(screen.getAllByText("REPRODUCED").length).toBeGreaterThan(0),
    );

    const malicious = "<img src=x onerror=alert(1)>";
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/config")) return Promise.resolve(jsonResponse(config));
      if (url.endsWith("/report")) return Promise.resolve(jsonResponse({ run_id: "fixture-run", content: malicious }));
      if (url.endsWith("/events")) return Promise.resolve(jsonResponse([]));
      return Promise.resolve(jsonResponse(detail));
    }));
    screen.getByRole("button", { name: "Load report" }).click();
    await waitFor(() => expect(screen.getByText(malicious)).toBeInTheDocument());
    expect(document.querySelector("img")).not.toBeInTheDocument();
  });
});

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
