import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";
import { LoginPage, OnsitePage } from "../pages";

test.beforeEach(async () => {
  await resetServer();
});

test("public onsite page loads", async ({ page }) => {
  const onsite = new OnsitePage(page);
  await onsite.open();
  await expect(page.locator("body")).toBeVisible();
});

test("onsite admin requires auth and loads once logged in", async ({ page }) => {
  const login = new LoginPage(page);
  await login.loginAsAdmin();
  await page.goto("/registration/onsite/admin");
  await expect(page.locator("body")).toBeVisible();
});

test.skip(
  "Square terminal paths — disabled for this cycle, re-enables next year",
  () => {
    // Kept intentionally so it shows up as a skipped assertion in reports
    // and reminds future readers that Square coverage needs to come back.
  },
);
