import { describe, expect, it } from "vitest";

import viteConfig from "../vite.config";
import { LOCAL_API_TARGET } from "./apiConfig";

describe("Vite local API proxy", () => {
  it("keeps dashboard development and preview traffic on the loopback API", async () => {
    const config = await resolveConfig("development");

    expect(config.base).toBe("/");
    expect(config.server).toMatchObject({
      host: "127.0.0.1",
      port: 5173,
      proxy: { "/api": LOCAL_API_TARGET },
    });
    expect(config.preview).toMatchObject({
      host: "127.0.0.1",
      port: 4173,
      proxy: { "/api": LOCAL_API_TARGET },
    });
  });

  it("uses the GitHub Pages base path for the backend-free public build", async () => {
    const config = await resolveConfig("public");

    expect(config.base).toBe("/ReproScout/");
  });
});

async function resolveConfig(mode: string) {
  if (typeof viteConfig !== "function") return viteConfig;
  return viteConfig({
    command: "serve",
    isPreview: false,
    isSsrBuild: false,
    mode,
  });
}
