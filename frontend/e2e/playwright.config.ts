import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

// This config lives in e2e/, not the frontend root, so `webServer.command` needs an explicit
// `cwd` back to frontend/ -- pnpm does not walk up looking for package.json the way git walks up
// looking for .git.
const FRONTEND_ROOT = path.resolve(import.meta.dirname, "..");
const PORT = 4173;
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: import.meta.dirname,
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // `build:e2e` compiles with VITE_API_BASE_URL="" so the bundle issues same-origin relative
    // requests (e.g. `/api/v1/suministros`) instead of absolute ones -- that lets
    // `page.route()` below intercept them without a real backend, and without a real backend
    // there is no CORS response to fake in the first place. See frontend/README.md, "Pruebas
    // E2E: por qué no hay backend real ni Service Worker de MSW".
    command: "pnpm run build:e2e && pnpm run preview:e2e",
    cwd: FRONTEND_ROOT,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
