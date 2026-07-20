import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The backend (`backend/src/energia/api/app.py`) has no CORS middleware configured, and
// backend/ is read-only reference for this project. Both the dev server and the preview
// server proxy `/api` to the backend on :8000 so a client configured with
// `VITE_API_BASE_URL=""` (empty string) can issue relative `/api/v1/...` requests that never
// cross an origin boundary in the browser -- see frontend/README.md, "Probar contra el backend
// real en desarrollo".
const backendProxy = {
  "/api": {
    target: "http://localhost:8000",
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: backendProxy,
  },
  preview: {
    proxy: backendProxy,
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    // e2e/**/*.spec.ts matches Vitest's default *.spec.ts glob but is Playwright-only (its own
    // test runner, its own config at e2e/playwright.config.ts) -- exclude it so `pnpm test`
    // does not try to execute it as a Vitest file.
    exclude: [...configDefaults.exclude, "e2e/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      // RNF-006 (docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md) mandates a minimum
      // 85% frontend test coverage -- see docs/03-architecture/adr/ADR-008-frontend-tooling.md.
      // This is a binding requirement already in force, not an arbitrary threshold picked for
      // this sprint (the backend's own RNF-level gate is 90%).
      thresholds: {
        lines: 85,
        statements: 85,
        functions: 85,
        branches: 85,
      },
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/vite-env.d.ts", "src/test/**", "**/*.d.ts", "**/types.ts"],
    },
  },
});
