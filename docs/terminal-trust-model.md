# Terminal trust model (onsite point-of-sale)

> Resolves SECURITY_REVIEW_2026-05-11 **MED-1** (OWASP API5 / ASVS V4.1.2):
> the function-level authorization design of the terminal endpoints was
> implemented but undocumented, blurring the "terminal" vs "staff" line.

## What a "terminal" is

A *terminal* (the `registration.models.Firebase` model — the name is
historical; it is a Postgres-backed onsite payment terminal, **no Firebase /
FCM SDK is involved**, see S17) is a physically-controlled point-of-sale
device at a convention registration desk. It authenticates to the server
with a **bearer token** (`Authorization: Bearer <token>`), stored hash-only
at rest (Decision #8 / HIGH-5: SHA-256, no plaintext column, generic 401 on
miss — `Firebase.find_by_token`).

## Two distinct capability tiers — by design

The onsite endpoints intentionally fall into two trust tiers. This is a
**kiosk model**, not an oversight:

| Tier | Auth | Endpoints (examples) | Capability |
|---|---|---|---|
| **Terminal-bearer** | Valid terminal bearer token only (`@csrf_exempt`, not `@staff_member_required`) | `terminal_square_token`, `complete_square_transaction` | Narrow, per-order: complete *the order opened at that terminal* |
| **Staff** | Authenticated staff session (`@staff_member_required`, plus `@permission_required` where money moves) | `complete_cash_transaction` (`order.cash` perm), cash-drawer actions (`order.cash` / `order.cash_admin`), `attendee_details` | Broader operator actions |

The terminal-bearer tier is deliberately *not* `@staff_member_required`: a
terminal is an unattended-capable device whose trust derives from physical
control of the desk plus possession of a per-device secret, not from an
interactive staff login. Requiring a staff session there would break the
kiosk operating model.

## Why this is safe (the capability is narrow, not blanket)

The terminal bearer token does **not** grant blanket order-write. It is
bounded by the **order ↔ terminal binding** (SECURITY_REVIEW HIGH-2,
implemented in commit `5710553`):

- An order records `Order.opened_at_terminal` the first time it is pushed to
  a terminal (`ordering.notify_terminal`).
- `complete_square_transaction` rejects (generic 404) any attempt to
  complete an order bound to a *different* terminal.
- `complete_cash_transaction` applies the same check against the active
  session terminal, failing **open** only when the active terminal cannot be
  resolved (cash is already `@staff_member_required` + `order.cash`-gated).
- The binding is fail-safe: legacy / null orders are unaffected, so enabling
  it cannot break an in-flight or historical checkout.

Net effect: a leaked or shared terminal token can only complete the orders
that were explicitly routed to *that* terminal — not arbitrary orders, and
not any staff-tier action.

## Operational rules

- A terminal token is a secret. Rotate it (`admin` → terminal → Rotate,
  superuser-only POST) if a device is lost, decommissioned, or suspected
  compromised. Rotation immediately invalidates the prior token.
- Never put a terminal token in source, a tracked env file, logs, or a
  screenshot. It is shown exactly once at provision time (Decision #8).
- Cash handling stays staff-tier (`order.cash` / `order.cash_admin`) and is
  audit-logged separately (LOW-4 / S33-j).
