import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./msw/server";

// MSW runs in Node mode (msw/node) for component/hook tests -- no browser service worker
// involved. The E2E smoke test uses Playwright's own page.route() instead (see e2e/), a
// separate, network-level mocking mechanism documented in README.md.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// `globals: false` in vite.config.ts keeps test files explicit about their imports, so
// Testing Library's DOM cleanup (unmounting components between tests) must be wired manually.
afterEach(cleanup);
