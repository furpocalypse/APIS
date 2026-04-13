import type { Page } from "@playwright/test";

/**
 * Onsite admin/terminal UI. Square terminal paths are intentionally
 * unexercised; the onsite spec drives cash-only transactions because
 * Square is disabled for this cycle.
 */
export class OnsitePage {
  constructor(public readonly page: Page) {}

  async open(): Promise<void> {
    await this.page.goto("/registration/onsite/");
  }

  async openAdmin(): Promise<void> {
    await this.page.goto("/registration/onsite/admin");
  }
}
