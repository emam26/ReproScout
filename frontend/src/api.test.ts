import { describe, expect, it, vi } from "vitest";

import {
  ApiError,
  ApiUnavailableError,
  isTerminalStage,
  requestJson,
  statusTone,
} from "./api";
import {
  API_HEALTH_PATH,
  API_PREFIX,
  LOCAL_API_TARGET,
} from "./apiConfig";

describe("status mapping", () => {
  it.each([
    ["REPRODUCED", "success"],
    ["PARTIAL", "partial"],
    ["BLOCKED", "blocked"],
    ["FAILED", "danger"],
    ["UNSAFE", "danger"],
    ["READY", "success"],
    ["UNVERIFIED", "partial"],
    ["MISSING_PREREQUISITES", "danger"],
    ["RUNNING", "neutral"],
  ])("maps %s to %s", (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  });

  it("stops polling only for terminal runs", () => {
    expect(isTerminalStage("DONE")).toBe(true);
    expect(isTerminalStage("EXECUTE")).toBe(false);
  });
});

describe("local API contract", () => {
  it("keeps the dashboard health path and loopback proxy target explicit", () => {
    expect(API_PREFIX).toBe("/api/v1");
    expect(API_HEALTH_PATH).toBe("/api/v1/health");
    expect(LOCAL_API_TARGET).toBe("http://127.0.0.1:8000");
  });

  it("preserves structured backend error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "unsafe input" }), { status: 400 }),
      ),
    );

    await expect(requestJson("/api/v1/runs/bad")).rejects.toEqual(
      new ApiError("unsafe input", 400),
    );
  });

  it("explains a proxy gateway failure when no local backend is available", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("bad gateway", { status: 502 })),
    );

    await expect(requestJson("/api/v1/health")).rejects.toBeInstanceOf(
      ApiUnavailableError,
    );
  });

  it("does not replace a meaningful gateway error from the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "upstream diagnostic" }), {
          status: 502,
        }),
      ),
    );

    await expect(requestJson("/api/v1/runs")).rejects.toEqual(
      new ApiError("upstream diagnostic", 502),
    );
  });

  it("treats transport failures as local API unavailability", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    await expect(requestJson("/api/v1/health")).rejects.toBeInstanceOf(
      ApiUnavailableError,
    );
  });

  it("rejects invalid successful responses instead of rendering them", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not-json", { status: 200 })),
    );

    await expect(requestJson("/api/v1/runs")).rejects.toEqual(
      new ApiError("The local API returned an invalid response.", 200),
    );
  });
});
