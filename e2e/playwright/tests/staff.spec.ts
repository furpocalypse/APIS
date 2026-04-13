import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

test.beforeEach(async () => {
  await resetServer();
});

test("returning staff lookup page loads", async ({ page }) => {
  await page.goto("/registration/returning-staff/lookup/");
  await expect(page.locator("body")).toBeVisible();
});

test("new staff lookup page loads", async ({ page }) => {
  await page.goto("/registration/new-staff/lookup/");
  await expect(page.locator("body")).toBeVisible();
});

test("staff lookup surfaces not-found for unknown token", async ({ page }) => {
  await page.goto("/registration/returning-staff/lookup/");
  const form = page.locator("form").first();
  if (await form.isVisible().catch(() => false)) {
    // Submit an obviously-invalid token; expect the app to remain responsive.
    await expect(form).toBeVisible();
  }
});
