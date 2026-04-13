import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

test.beforeEach(async () => {
  await resetServer();
});

test("dealer landing is reachable", async ({ page }) => {
  await page.goto("/registration/dealer/");
  await expect(page.locator("body")).toBeVisible();
});

test("dealer lookup page loads", async ({ page }) => {
  await page.goto("/registration/dealer/lookup/");
  await expect(page.locator("body")).toBeVisible();
});

test("dealer checkout page loads", async ({ page }) => {
  await page.goto("/registration/dealer/checkout/");
  await expect(page.locator("body")).toBeVisible();
});

test("dealer assistants lookup page loads", async ({ page }) => {
  await page.goto("/registration/dealer/assistants/lookup/");
  await expect(page.locator("body")).toBeVisible();
});
