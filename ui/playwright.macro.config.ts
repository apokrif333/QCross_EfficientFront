import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
import base from "./playwright.config";
const root = resolve(import.meta.dirname, "..");
const python =
  process.env.QCROSS_PYTHON ??
  resolve(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
export default defineConfig({
  ...base,
  use: {
    ...base.use,
    baseURL: "http://127.0.0.1:5175",
    channel: process.platform === "win32" ? "chrome" : undefined,
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8002`,
      cwd: root,
      url: "http://127.0.0.1:8002/health",
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: "npm run dev -- --port 5175",
      url: "http://127.0.0.1:5175",
      env: { QCROSS_API_URL: "http://127.0.0.1:8002" },
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
