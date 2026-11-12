/** Shared local API contract for the dashboard and Vite development proxy. */

export const API_PREFIX = "/api/v1";
export const API_HEALTH_PATH = `${API_PREFIX}/health`;
export const LOCAL_API_TARGET = "http://127.0.0.1:8000";

export const LOCAL_API_SETUP_GUIDANCE =
  "The local ReproScout API is unavailable. Install API support with " +
  '`uv tool install --reinstall --editable ".[api]"` (or ' +
  '`python -m pip install -e ".[api]"`), then start `reproscout serve` ' +
  "and keep it running on 127.0.0.1:8000.";
