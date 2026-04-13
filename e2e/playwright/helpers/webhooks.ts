import { request as apiRequest } from "@playwright/test";
import { baseURL } from "./api";

export type PayPalEventType =
  | "PAYMENT.CAPTURE.COMPLETED"
  | "PAYMENT.CAPTURE.REFUNDED"
  | "PAYMENT.CAPTURE.REVERSED"
  | "PAYMENT.CAPTURE.DENIED"
  | "CUSTOMER.DISPUTE.CREATED"
  | "CUSTOMER.DISPUTE.UPDATED"
  | "CUSTOMER.DISPUTE.RESOLVED";

export interface WebhookPayload {
  event_type: PayPalEventType;
  resource: Record<string, unknown>;
  id?: string;
}

/**
 * POST a synthetic PayPal webhook to the APIS webhook endpoint. The server
 * bypasses signature verification when ``E2E_MODE`` is on and the
 * ``X-E2E-Mock-Signature`` header equals ``"1"``.
 */
export async function postPayPalWebhook(
  payload: WebhookPayload,
): Promise<void> {
  const api = await apiRequest.newContext({ baseURL: baseURL() });
  try {
    const body = {
      id: payload.id ?? `WH-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      event_type: payload.event_type,
      create_time: new Date().toISOString(),
      resource_type: inferResourceType(payload.event_type),
      summary: `Synthetic ${payload.event_type}`,
      resource: payload.resource,
    };
    const res = await api.post("/registration/webhook/paypal/v1", {
      data: body,
      headers: {
        "Content-Type": "application/json",
        "X-E2E-Mock-Signature": "1",
      },
    });
    if (!res.ok()) {
      throw new Error(
        `webhook POST failed: ${res.status()} ${await res.text().catch(() => "")}`,
      );
    }
  } finally {
    await api.dispose();
  }
}

function inferResourceType(event: PayPalEventType): string {
  if (event.startsWith("CUSTOMER.DISPUTE")) return "dispute";
  if (event.startsWith("PAYMENT.CAPTURE")) return "capture";
  return "unknown";
}
