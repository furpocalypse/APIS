import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { mountPayPalStub } from "../helpers/stub";

/**
 * Wraps the checkout template. Callers ``open()`` it on a cart that already
 * has items, the template loads the (stubbed) PayPal SDK, renders the
 * stubbed button, and ``payWithStubbedPayPal()`` clicks it to simulate an
 * approved transaction.
 */
export class CheckoutPage {
  constructor(public readonly page: Page) {}

  async open(path = "/registration/cart/checkout/"): Promise<void> {
    await mountPayPalStub(this.page);
    await this.page.goto(path);
  }

  async fillBilling(opts: {
    firstName?: string;
    lastName?: string;
    email?: string;
    address1?: string;
    city?: string;
    state?: string;
    country?: string;
    postal?: string;
  }): Promise<void> {
    if (opts.firstName) await this.page.fill("#fname", opts.firstName);
    if (opts.lastName) await this.page.fill("#lname", opts.lastName);
    if (opts.email) await this.page.fill("#email", opts.email);
    if (opts.address1) await this.page.fill("#add1", opts.address1);
    if (opts.city) await this.page.fill("#city", opts.city);
    if (opts.country)
      await this.page.selectOption("#country", opts.country);
    if (opts.state) await this.page.selectOption("#state", opts.state);
    if (opts.postal)
      await this.page.evaluate(
        (v) => {
          const el = document.getElementById("postal") as HTMLInputElement | null;
          if (el) el.value = v;
        },
        opts.postal,
      );
  }

  async payWithStubbedPayPal(): Promise<void> {
    const btn = this.page.getByTestId("paypal-mock-button");
    await expect(btn).toBeVisible({ timeout: 15_000 });
    await btn.click();
    await this.page.waitForURL(/\/registration\/cart\/done\/?$/, {
      timeout: 20_000,
    });
  }
}
