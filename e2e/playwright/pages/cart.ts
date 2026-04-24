import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

/**
 * Thin wrapper around the attendee-facing cart flow. The entry point
 * ``/registration/`` renders a Solid.js SPA that posts cart lines to
 * ``/registration/cart/add/``; we drive that via the page's own exposed
 * helpers where we can, and fall back to direct POST for setup-style calls
 * from tests where clicking through the SPA is not what's under test.
 */
export class CartPage {
  constructor(public readonly page: Page) {}

  async open(): Promise<void> {
    await this.page.goto("/registration/");
    await expect(this.page).toHaveURL(/\/registration\/?$/);
  }

  async openCart(): Promise<void> {
    await this.page.goto("/registration/cart/");
  }

  async applyDiscount(code: string): Promise<void> {
    await this.page.goto("/registration/cart/checkout/");
    await this.page.fill("#discount", code);
    await this.page.click("#apply_discount");
  }

  async proceedToCheckout(): Promise<void> {
    await this.page.goto("/registration/cart/checkout/");
  }

  async getTotal(): Promise<number> {
    const raw = await this.page.textContent("#totalAmount");
    return raw ? parseFloat(raw.replace(/[^0-9.]/g, "")) : 0;
  }
}
