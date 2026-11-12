import { describe, expect, it, vi } from "vitest";

import { ApiError, isTerminalStage, requestJson, statusTone } from "./api";

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

describe("API client", () => {
  it("translates structured API errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "unsafe input" }), { status: 400 }),
      ),
    );

    await expect(requestJson("/api/v1/runs/bad")).rejects.toEqual(
      new ApiError("unsafe input", 400),
    );
    vi.unstubAllGlobals();
  });

  it("rejects invalid JSON responses as an API failure rather than rendering them", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not-json", { status: 200 })),
    );

    await expect(requestJson("/api/v1/runs")).resolves.toBeNull();
    vi.unstubAllGlobals();
  });
});
