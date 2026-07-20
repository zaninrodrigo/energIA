import { test, expect } from "@playwright/test";
import { lotesFixture, rankingFixture, suministrosFixture } from "../src/test/fixtures";

// Smoke coverage for both screens, against a mocked API (no real backend involved -- see
// playwright.config.ts). "/" now redirects to the Ranking de Riesgo dashboard (this project's
// demo centerpiece, App.tsx); Suministros is reached via the primary nav. A real-backend E2E
// (real pipeline, real database) is a separate, manual validation step -- see README.md.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/suministros**", async (route) => {
    await route.fulfill({ json: suministrosFixture });
  });
  await page.route("**/api/v1/lotes**", async (route) => {
    await route.fulfill({ json: lotesFixture });
  });
  await page.route("**/api/v1/motor/lotes/**/resultados**", async (route) => {
    await route.fulfill({ json: rankingFixture });
  });
});

test("the app loads at / and renders the Ranking de Riesgo dashboard with API data", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Ranking de Riesgo" })).toBeVisible();
  await expect(page.getByText("Total analizados")).toBeVisible();
  await expect(page.getByText(rankingFixture.items[0].numero_suministro)).toBeVisible();
});

test("navigating to Suministros renders the suministros table with API data", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Suministros" }).click();

  await expect(page.getByRole("heading", { name: "Suministros" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Número de suministro" })).toBeVisible();
  await expect(page.getByText(suministrosFixture.items[0].numero_suministro)).toBeVisible();
  await expect(
    page.getByText(`Mostrando 1–${suministrosFixture.items.length} de ${suministrosFixture.total}`),
  ).toBeVisible();
});
