import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";
import { LoginPage, AdminOrderPage } from "../pages";

test.beforeEach(async () => {
  await resetServer();
});

test("admin can view the order list", async ({ page }) => {
  const login = new LoginPage(page);
  await login.loginAsAdmin();
  const admin = new AdminOrderPage(page);
  await admin.list();
  await expect(page).toHaveURL(/\/admin\/registration\/order\//);
});

test("admin users list loads", async ({ page }) => {
  const login = new LoginPage(page);
  await login.loginAsAdmin();
  await page.goto("/admin/auth/user/");
  await expect(page).toHaveURL(/\/admin\/auth\/user\//);
});
