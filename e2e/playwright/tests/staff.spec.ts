import { test, expect } from "@playwright/test";
import { resetServer } from "../helpers/api";

test.beforeEach(async () => {
  await resetServer();
});

// ``/registration/{returning,new}-staff/lookup/`` are JSON POST endpoints,
// not GET-renderable pages — the lookup form lives in the SPA. These tests
// exercise the endpoints directly and assert the server responds without
// crashing.

test("returning staff lookup responds to unknown token", async ({
  request,
}) => {
  const res = await request.post("/registration/returning-staff/lookup/", {
    headers: { "Content-Type": "application/json" },
    data: { email: "nobody@example.test", token: "not-a-real-token" },
  });
  // CSRF rejection (403) or invalid-token (404) are both acceptable; the
  // point is the server answered.
  expect([403, 404]).toContain(res.status());
});

test("new staff lookup responds to unknown token", async ({ request }) => {
  const res = await request.post("/registration/new-staff/lookup/", {
    headers: { "Content-Type": "application/json" },
    data: { email: "nobody@example.test", token: "not-a-real-token" },
  });
  expect([403, 404]).toContain(res.status());
});
