import type { Page } from "@playwright/test";

/**
 * Django admin order/refund workflows. The admin is the built-in Django
 * admin; refunds happen through the custom ``registration.admin`` actions
 * registered against the ``Order`` model.
 */
export class AdminOrderPage {
  constructor(public readonly page: Page) {}

  async list(): Promise<void> {
    await this.page.goto("/admin/registration/order/");
  }

  async open(orderId: number | string): Promise<void> {
    await this.page.goto(`/admin/registration/order/${orderId}/change/`);
  }
}
