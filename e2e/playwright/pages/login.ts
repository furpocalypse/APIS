import type { Page } from "@playwright/test";

/**
 * django-allauth login flow. Uses the default allauth templates wired up
 * under ``/accounts/``.
 */
export class LoginPage {
  constructor(public readonly page: Page) {}

  async openAdmin(): Promise<void> {
    await this.page.goto("/admin/login/");
  }

  async loginAsAdmin(
    username = "e2e-admin",
    password = "e2e-admin-password",
  ): Promise<void> {
    await this.openAdmin();
    await this.page.fill("input[name=login]", username);
    await this.page.fill("input[name=password]", password);
    await this.page.getByRole("button", { name: "Sign In", exact: true }).click();
  }
}
