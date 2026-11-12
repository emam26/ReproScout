import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { LOCAL_API_TARGET } from "./src/apiConfig";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === "public" ? "/ReproScout/" : "/",
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": LOCAL_API_TARGET,
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": LOCAL_API_TARGET,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/testSetup.ts",
    globals: true,
  },
}));
