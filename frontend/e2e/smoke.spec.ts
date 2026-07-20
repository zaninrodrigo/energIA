import { test, expect } from "@playwright/test";
import { suministrosFixture } from "../src/test/fixtures";

// Sprint 0's only E2E scenario: the app boots and renders the Suministros screen end to end
// (routing, TanStack Query, the table) against a mocked API -- no real backend involved (see
// playwright.config.ts). A real-backend E2E lands with the Dashboard Ejecutivo slice, once there
// is more than one screen to justify the extra CI cost of a live database.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/suministros**", async (route) => {
    await route.fulfill({ json: suministrosFixture });
  });
});

test("the app loads and renders the suministros table with API data", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /suministros/i })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Número de suministro" })).toBeVisible();
  await expect(page.getByText(suministrosFixture.items[0].numero_suministro)).toBeVisible();
  await expect(
    page.getByText(`Mostrando 1–${suministrosFixture.items.length} de ${suministrosFixture.total}`),
  ).toBeVisible();
});
