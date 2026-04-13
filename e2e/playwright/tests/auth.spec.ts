import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

test.beforeEach(async () => {
  await resetServer();
});

test("admin login succeeds with seeded superuser", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=login]", "e2e-admin");
  await page.fill("input[name=password]", "e2e-admin-password");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/?$/);
});

test("admin login fails with wrong password", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=login]", "e2e-admin");
  await page.fill("input[name=password]", "not-the-password");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  // /admin/login/ is wrapped by allauth's secure_admin_login, so a failed
  // submit re-renders allauth's form at /accounts/login/.
  await expect(page).toHaveURL(/\/(admin|accounts)\/login/);
});

test("logout clears the session", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=login]", "e2e-admin");
  await page.fill("input[name=password]", "e2e-admin-password");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/?$/);
  // django.contrib.auth.views.LogoutView is POST-only since Django 5.0; a
  // GET returns 405 (firefox surfaces that as NS_ERROR_NET_EMPTY_RESPONSE).
  // Post with the CSRF token pulled from the session cookie.
  const csrf = (await page.context().cookies()).find(
    (c) => c.name === "csrftoken",
  );
  const logout = await page.request.post("/registration/logout/", {
    headers: { "X-CSRFToken": csrf?.value ?? "" },
  });
  expect(logout.ok() || logout.status() === 302).toBeTruthy();
  // After logout, hitting /admin/ redirects to the login.
  await page.goto("/admin/");
  await expect(page).toHaveURL(/\/(admin|accounts)\/login/);
});
