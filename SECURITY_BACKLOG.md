# Security Backlog — Planned / Deferred

Open security work that has been audited but not yet implemented. Each
item has the same format as the audit reports: scope, risk, what
implementing it requires, and why it isn't done yet.

Items in this file are **planned**, not "ignored". They have known
remediation paths and are tracked here so the next reviewer / PR author
picks them up with full context rather than rediscovering the issue.

---

## P1 #5 — Webhook order-completion race (Order.status writers + capacity counters)

**Status:** P1 race CLOSED in-band (S24, commit `ebb766e`). The fused
compare-and-set primitive `registration.payments.transition_order_status`
now exists; the documented race (duplicate `PAYMENT.CAPTURE.COMPLETED`
→ duplicate registration email + capacity double-decrement) is fixed and
proven by `test_concurrency.py::TestWebhookOrderCompletionRace` (CI). The
synchronous-checkout finalization path was migrated; the auto-expiry job
was confirmed already race-safe (Pattern A). **Residual sweep (tracked,
non-P1):** the secondary Square webhook refund/dispute processors
(`payments.process_webhook_*`) and the synchronous PayPal/Square
capture+refresh writers still set status directly — these are lower
severity (refund/dispute double-application, partly idempotent via the
existing `refund_exists` skip + the durable `PaymentWebhookNotification
(integration, event_id)` ingestion dedup). They migrate through the
now-available primitive (Pattern A row-lock for the async processors;
`expected`/`exclude` CAS for the synchronous ones) as bounded follow-up
work; the enabling primitive + the security-load-bearing P1 fix are
landed. Audited 2026-05-10; primitive landed 2026-05-17.

**Original assessment (retained for context):**

**Risk:** Two webhook deliveries for the same order arriving on different
gunicorn workers within milliseconds can both pass the read-time
"already COMPLETED?" guard, both call `order.save()`, and both queue
`send_registration_email_task` — user receives duplicate emails and
`PriceLevel.remainingSlots`/`reservedSlots` are double-decremented.
OWASP A04 (Insecure Design), ASVS V11.1.2.

**Blast radius:** user-visible (duplicate email, off-by-one capacity).
Not exploitable by an attacker — requires the provider to deliver two
events within the worker-process scheduling window.

**Why deferred:** a partial fix is worse than no fix. 8+ writers
of `Order.status` exist across the codebase; missing any leaves the
race open while operators assume it's closed. Also the parallel
`PriceLevel.remainingSlots` / `reservedSlots` counter race is its own
TOCTOU surface that must be closed in the same PR.

### Inventory of writers to migrate

```
registration/paypal_webhook_handlers.py   # ~10 handlers (capture, refund, dispute, …)
registration/paypal_payments.py            # synchronous capture flow
registration/payments.py                   # Square synchronous charge + refund
registration/views/ordering.py             # checkout finalization paths
registration/views/onsite_admin.py         # cash transactions, admin actions
registration/admin.py                      # admin custom actions
registration/management/commands/expire_pending_orders.py
registration/tasks.py                      # Celery tasks (refund, etc.)
```

Plus the capacity counter sites:
```
registration/models.py    # PriceLevel.remainingSlots / reservedSlots accessors
registration/signals.py   # capacity update_capacity_for_status_change call sites
```

### Recommended pattern per writer

- **External-API in critical path** (PayPal sync capture, Square charge):
  Pattern B (`Order.objects.filter(pk=..., status=PREV).update(status=NEXT)`)
  so no lock is held across the HTTP call.
- **Pure DB + Celery side effects** (webhook handlers, expiry job,
  admin actions): Pattern A
  (`transaction.atomic()` + `select_for_update()`).
- Every Celery dispatch must move inside `transaction.on_commit(...)` so
  a transaction rollback doesn't fire the side effect.
- Capacity counter writes migrate to `F()` expressions in the same
  transaction, e.g.:
  ```python
  PriceLevel.objects.filter(pk=pk).update(remainingSlots=F("remainingSlots") - 1)
  ```

### Required regression test

A `test_concurrency.py` case (`registration/tests/` already has one) that:

1. Opens a second `psycopg` connection to the test database.
2. Starts two transactions interleaving read-check-write on the same
   Order row, simulating two webhook handlers landing in parallel.
3. Asserts end state: `status=COMPLETED`, `email_sent` set exactly once,
   `PriceLevel.remainingSlots` decremented by exactly one,
   `tasks.send_registration_email_task.delay` mock called exactly once.

Without this test, the fix can't be proven correct by code review alone.

### Estimated effort

4–8 hours for an experienced Django dev: inventory pass + write the
pattern-A / pattern-B refactor + capacity counter migration + concurrency
test + `make test-django` validation + reviewer round-trip.

### When this becomes more urgent

- If telemetry shows the duplicate-email pattern firing in production.
- If a capacity-counter drift is observed (`remainingSlots` doesn't
  match the order count).
- Before any major refactor of `Order` model or webhook handlers — fold
  the race fix into that work instead of doing it twice.
