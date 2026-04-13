import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

test.beforeEach(async () => {
  await resetServer();
});

test("admin login succeeds with seeded superuser", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=username]", "e2e-admin");
  await page.fill("input[name=password]", "e2e-admin-password");
  await page.click("input[type=submit]");
  await expect(page).toHaveURL(/\/admin\/?$/);
});

test("admin login fails with wrong password", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=username]", "e2e-admin");
  await page.fill("input[name=password]", "not-the-password");
  await page.click("input[type=submit]");
  // Django re-renders the login page with an error and stays on /admin/login/
  await expect(page).toHaveURL(/\/admin\/login/);
});

test("logout clears the session", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=username]", "e2e-admin");
  await page.fill("input[name=password]", "e2e-admin-password");
  await page.click("input[type=submit]");
  await expect(page).toHaveURL(/\/admin\/?$/);
  await page.goto("/registration/logout/");
  // After logout, hitting /admin/ redirects to the login.
  await page.goto("/admin/");
  await expect(page).toHaveURL(/\/admin\/login/);
});
