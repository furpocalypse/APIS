import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";
import { postPayPalWebhook } from "../helpers/webhooks";

test.beforeEach(async () => {
  await resetServer();
});

test("capture completed webhook is accepted", async () => {
  await postPayPalWebhook({
    event_type: "PAYMENT.CAPTURE.COMPLETED",
    resource: {
      id: "CAP-E2E-1",
      status: "COMPLETED",
      amount: { currency_code: "USD", value: "45.00" },
      invoice_id: "nonexistent-reference",
      custom_id: "nonexistent-reference",
    },
  });
});

test("capture refunded webhook is accepted", async () => {
  await postPayPalWebhook({
    event_type: "PAYMENT.CAPTURE.REFUNDED",
    resource: {
      id: "REF-E2E-1",
      status: "COMPLETED",
      amount: { currency_code: "USD", value: "45.00" },
      links: [
        {
          rel: "up",
          href: "https://api.paypal.com/v2/payments/captures/CAP-E2E-1",
        },
      ],
    },
  });
});

test("dispute created webhook is accepted", async () => {
  await postPayPalWebhook({
    event_type: "CUSTOMER.DISPUTE.CREATED",
    resource: {
      dispute_id: "PP-D-1001",
      status: "OPEN",
      disputed_transactions: [
        { seller_transaction_id: "CAP-E2E-1" },
      ],
    },
  });
});

test("unknown event type still persists the notification", async ({ request }) => {
  const res = await request.post("/registration/webhook/paypal/v1", {
    headers: {
      "Content-Type": "application/json",
      "X-E2E-Mock-Signature": "1",
    },
    data: {
      id: "WH-UNKNOWN-1",
      event_type: "SOMETHING.NEW",
      resource_type: "capture",
      summary: "no handler yet",
      resource: {},
    },
  });
  expect(res.ok()).toBeTruthy();
});

test("request without mock signature header is rejected", async ({
  request,
}) => {
  const res = await request.post("/registration/webhook/paypal/v1", {
    headers: { "Content-Type": "application/json" },
    data: {
      id: "WH-NO-SIG",
      event_type: "PAYMENT.CAPTURE.COMPLETED",
      resource: {},
    },
  });
  // verify_signature fails closed when headers/config are missing.
  expect(res.status()).toBe(403);
});

test("capture denied webhook is accepted", async () => {
  await postPayPalWebhook({
    event_type: "PAYMENT.CAPTURE.DENIED",
    resource: {
      id: "CAP-E2E-DENY",
      status: "DECLINED",
      amount: { currency_code: "USD", value: "45.00" },
      invoice_id: "nonexistent-reference",
      custom_id: "nonexistent-reference",
    },
  });
});

test("capture completed webhook is idempotent on replay", async () => {
  const body = {
    event_type: "PAYMENT.CAPTURE.COMPLETED" as const,
    id: "WH-IDEMPOTENT-1",
    resource: {
      id: "CAP-E2E-IDEM",
      status: "COMPLETED",
      amount: { currency_code: "USD", value: "45.00" },
      invoice_id: "nonexistent-reference",
      custom_id: "nonexistent-reference",
    },
  };
  await postPayPalWebhook(body);
  // Replay of the same event_id should still return 200 (notification is
  // deduped on event_id).
  await postPayPalWebhook(body);
});
