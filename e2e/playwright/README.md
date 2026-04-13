# APIS Playwright suite

Browser-level end-to-end tests for the APIS registration app. Exercises every
user-visible flow (attendee, staff, dealer, upgrades, onsite, admin refunds,
webhooks, auth) against a locally-orchestrated stack. PayPal is mocked at two
layers:

- The PayPal JS SDK load (`https://www.paypal.com/sdk/js`) is intercepted by
  Playwright and replaced with a stub that auto-approves orders.
- `registration/paypal_payments.py` swaps its `orders_controller` and
  `payments_controller` for an in-process Python stub when the server is
  launched with `E2E_MODE=1`.

Square is **not** tested — it is kept in the codebase and gated off, to be
re-enabled next year.

## First-time setup

```bash
make e2e-setup
```

Installs `@playwright/test` and downloads the chromium + firefox browsers.

## Run the full suite locally

```bash
make e2e
```

That target:

1. Brings up Postgres, Redis, and Gotenberg via docker compose.
2. Runs migrations and loads `fixtures/seed.json`.
3. Launches a Django dev server with `E2E_MODE=1` on port 8000.
4. Runs `npx playwright test`.
5. Tears down the dev server.

HTML report lands under `e2e/playwright/playwright-report/`. Open it with:

```bash
cd e2e/playwright && npx playwright show-report
```

## Fast feedback loop

```bash
make e2e-smoke   # @smoke-tagged tests only (<2 min)
make e2e-ui      # interactive Playwright UI
```

## Layout

```
e2e/playwright/
  fixtures/
    seed.json             Django loaddata fixture: Event, PriceLevels, Discount,
                          TableSize, admin user.
    paypal-sdk-stub.js    Replacement for the live PayPal JS SDK.
  helpers/
    stub.ts               page.route() wiring for the SDK stub.
    webhooks.ts           POST synthetic PayPal webhook events to the server.
    api.ts                Reset helpers: POST /e2e/reset/, GET /e2e/order/…/.
  pages/                  Page Object Models (CartPage, CheckoutPage, …).
  tests/                  Spec files — one per major user flow.
  scripts/
    up.sh                 Bring services up, migrate, seed, launch server.
    down.sh               Kill the server + clean up.
```

## Adding tests

1. Import the relevant Page Object from `pages/`.
2. Call `resetServer()` from `helpers/api.ts` in a `test.beforeEach` so each
   test starts clean.
3. Attach the PayPal SDK stub via `mountPayPalStub(page)` in any test that
   reaches a checkout page.
4. Tag the happy path variant with `@smoke` so it runs in the fast suite.
