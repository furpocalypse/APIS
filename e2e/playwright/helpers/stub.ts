import { readFile } from "node:fs/promises";
import path from "node:path";
import type { Page } from "@playwright/test";

const STUB_PATH = path.resolve(__dirname, "..", "fixtures", "paypal-sdk-stub.js");
let cached: string | null = null;

async function stubBody(): Promise<string> {
  if (cached === null) {
    cached = await readFile(STUB_PATH, "utf-8");
  }
  return cached;
}

/**
 * Intercept every request to paypal.com and serve a local stub script for the
 * JS SDK load. Anything else under paypal.com is short-circuited to an empty
 * 204 so the browser never reaches out to the real PayPal for any reason.
 */
export async function mountPayPalStub(page: Page): Promise<void> {
  const body = await stubBody();
  await page.route(/https?:\/\/(www\.)?paypal\.com\/sdk\/js.*/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body,
    }),
  );
  await page.route(/https?:\/\/([a-z0-9.-]+\.)?paypal\.com\/.*/, (route) => {
    // Anything else PayPal-hosted (tracking pixels, telemetry) gets 204'd.
    if (/\/sdk\/js/.test(route.request().url())) {
      return route.fallback();
    }
    return route.fulfill({ status: 204, body: "" });
  });
}
