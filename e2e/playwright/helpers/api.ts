import type { APIRequestContext } from "@playwright/test";
import { request as apiRequest } from "@playwright/test";

export function baseURL(): string {
  return process.env.APIS_BASE_URL ?? "http://127.0.0.1:8000";
}

async function ctx(): Promise<APIRequestContext> {
  return apiRequest.newContext({ baseURL: baseURL() });
}

/**
 * Wipe volatile DB state on the Django server (orders, carts, attendees,
 * webhook notifications) and reset the in-process PayPal stub. Call from
 * test.beforeEach to guarantee isolation.
 */
export async function resetServer(): Promise<void> {
  const api = await ctx();
  try {
    const res = await api.post("/registration/e2e/reset/");
    if (!res.ok()) {
      throw new Error(
        `reset failed: ${res.status()} ${await res.text().catch(() => "")}`,
      );
    }
  } finally {
    await api.dispose();
  }
}

export interface OrderState {
  found: boolean;
  reference?: string;
  status?: string;
  total?: string;
  billingType?: string;
  lastFour?: string;
  apiData?: unknown;
  notes?: string;
}

export async function getOrderState(reference: string): Promise<OrderState> {
  const api = await ctx();
  try {
    const res = await api.get(
      `/registration/e2e/order/${encodeURIComponent(reference)}/`,
    );
    if (res.status() === 404) return { found: false };
    if (!res.ok()) {
      throw new Error(`order state fetch failed: ${res.status()}`);
    }
    return (await res.json()) as OrderState;
  } finally {
    await api.dispose();
  }
}

export async function getPayPalSnapshot(
  paypalOrderId: string,
): Promise<unknown> {
  const api = await ctx();
  try {
    const res = await api.get(
      `/registration/e2e/paypal/${encodeURIComponent(paypalOrderId)}/`,
    );
    if (!res.ok()) {
      throw new Error(`paypal snapshot fetch failed: ${res.status()}`);
    }
    const data = (await res.json()) as { snapshot: unknown };
    return data.snapshot;
  } finally {
    await api.dispose();
  }
}
