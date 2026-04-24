import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

/**
 * Tags: @smoke
 *
 * One-sentence happy-path per major flow. Keep this file under two minutes
 * wall-clock so it's viable as a gate in the quick CI loop.
 */

test.beforeEach(async () => {
  await resetServer();
});

test("@smoke landing page renders", async ({ page }) => {
  await page.goto("/registration/");
  await expect(page).toHaveURL(/\/registration\/?$/);
});

test("@smoke admin login redirects to admin index", async ({ page }) => {
  await page.goto("/admin/login/?next=/admin/");
  await page.fill("input[name=login]", "e2e-admin");
  await page.fill("input[name=password]", "e2e-admin-password");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/?$/);
});

test("@smoke onsite page is reachable", async ({ page }) => {
  await page.goto("/registration/onsite/");
  await expect(page.locator("body")).toBeVisible();
});

test("@smoke dealer landing is reachable", async ({ page }) => {
  await page.goto("/registration/dealer/");
  await expect(page.locator("body")).toBeVisible();
});

test("@smoke paypal webhook accepts mock-signed event", async ({ request }) => {
  const res = await request.post("/registration/webhook/paypal/v1", {
    headers: {
      "Content-Type": "application/json",
      "X-E2E-Mock-Signature": "1",
    },
    data: {
      id: "WH-SMOKE-1",
      event_type: "PAYMENT.CAPTURE.COMPLETED",
      resource_type: "capture",
      summary: "smoke",
      resource: { id: "CAP-SMOKE-1", status: "COMPLETED" },
    },
  });
  expect(res.ok()).toBeTruthy();
});
