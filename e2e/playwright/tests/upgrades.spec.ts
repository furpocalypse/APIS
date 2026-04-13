import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

test.beforeEach(async () => {
  await resetServer();
});

test("upgrade lookup page loads", async ({ page }) => {
  await page.goto("/registration/upgrade/lookup/");
  await expect(page.locator("body")).toBeVisible();
});

test("upgrade checkout page loads", async ({ page }) => {
  await page.goto("/registration/upgrade/checkout/");
  await expect(page.locator("body")).toBeVisible();
});
