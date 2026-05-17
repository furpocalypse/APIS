# Canonical CI / test settings (Decision #1/#7). Thin specialization of the
# single tracked env-driven base; overrides ONLY test-only deltas. Selected
# via DJANGO_SETTINGS_MODULE=fm_eventmanager.settings_test (Makefile TEST_ENV,
# e2e/playwright/scripts/up.sh, .github/workflows). Inherits the base's
# binding invariants (S2b E2E_MODE, D-IP header, S35 logging) — those are
# keyed off APIS_ENV=production, never set in CI, so they no-op here.
import os

from .settings_base import *  # noqa: F401,F403

# --- Test-only deltas (was: standalone settings_test.py divergences) ------

# WhiteNoise serves /static/ from gunicorn in prod; the test runner never
# serves static, and the manifest-strict path adds avoidable CI fragility.
# Drop the middleware for the suite (parity with the prior settings_test).
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405

# collectstatic in CI cannot write the prod path; TEST_ENV sets STATIC_ROOT.
STATIC_ROOT = os.getenv("STATIC_ROOT", "/tmp/apis-test-static")

# Payment providers default to sandbox in tests; PayPal creds optional in CI
# (base reads them via os.environ.get(..., "")).
SQUARE_ENVIRONMENT = os.getenv("SQUARE_ENVIRONMENT", "sandbox")
PAYPAL_ENVIRONMENT = os.getenv("PAYPAL_ENVIRONMENT", "sandbox")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")

# Celery executes eagerly so .delay() side effects (mail.outbox, mocked
# tasks) resolve before the HTTP response. Base already defaults eager;
# pin it here so the test contract is explicit and reload-safe.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Existing webhook unit-test fixtures embed fixed historical timestamps
# (2022-2025). The age-window bound is exercised precisely by the
# freezegun TestWebhookAgeWindow; for the rest of the suite widen the past
# bound so static fixtures aren't rejected (replay defence in tests is the
# PaymentWebhookNotification dedup, plan S38). Keep a real future-skew.
WEBHOOK_MAX_AGE_SECONDS = int(os.getenv("WEBHOOK_MAX_AGE_SECONDS", str(60 * 60 * 24 * 365 * 100)))
WEBHOOK_FUTURE_SKEW_SECONDS = int(os.getenv("WEBHOOK_FUTURE_SKEW_SECONDS", "300"))
