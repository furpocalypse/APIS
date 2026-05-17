# Canonical CI / test settings (Decision #1/#7). Thin specialization of the
# single tracked env-driven base; overrides ONLY test-only deltas. Selected
# via DJANGO_SETTINGS_MODULE=fm_eventmanager.settings_test (Makefile TEST_ENV,
# e2e/playwright/scripts/up.sh, .github/workflows). Inherits the base's
# binding invariants (S2b E2E_MODE, D-IP header, S35 logging) — those are
# keyed off APIS_ENV=production, never set in CI, so they no-op here.
import base64
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

# mqtt.get_token() does base64.b64decode(settings.MQTT_JWT_SECRET) then
# jwt.encode(...). The base reads MQTT_JWT_SECRET from the environment
# (a real secret in prod); neither the Makefile TEST_ENV nor the CI
# django.yml env sets it, so without a test-only default any test that
# reaches get_token (terminal provisioning/QR — S17/S21) raises
# `TypeError: ... not 'NoneType'` in b64decode. Provide a deterministic,
# valid-base64 HS256 key for the suite (env-overridable). Test-only:
# settings_base never assigns this, so prod is unaffected.
MQTT_JWT_SECRET = os.getenv(
    "MQTT_JWT_SECRET",
    base64.b64encode(b"apis-test-only-mqtt-jwt-hs256-secret-key").decode(),
)

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

# --- django-axes: test-runner posture (S3 / S6 / S10) --------------------
# PR #41 added "axes.backends.AxesStandaloneBackend" as the FIRST entry of
# AUTHENTICATION_BACKENDS (settings_base). Django's test client
# `Client.login()` (django/test/client.py: `authenticate(**credentials)`)
# calls authenticate() WITHOUT a request by design. django-axes 8.3.1 wraps
# `AxesStandaloneBackend.authenticate` with `@toggleable` (axes/helpers.py:
# `inner` -> "if settings.AXES_ENABLED: return func(...)"); with AXES_ENABLED
# False the backend never runs and authenticate() falls through to
# ModelBackend — exactly the pre-PR-#41 behaviour. With it True and no
# request, axes/backends.py:46 raises AxesBackendRequestParameterRequired,
# which is why all ~40 admin/onsite/printing tests that use
# `self.client.login()` (a session-establishment scaffold, not an auth test)
# ERROR. This flag is defined ONLY in this test module — it is never read by
# settings_base, so production (untracked settings.py -> settings_base, axes
# default True, APIS_ENV=production guards) is unaffected: the S3 BLOCKING
# non-regression acceptance (no prod AXES_FAILURE_LIMIT change, no
# RequireClientIP/CSRF change) holds by construction (grep-verifiable: this
# name appears only here). Real axes lockout behaviour at the configured
# limit is positively proven by registration.tests.test_middleware
# .TestAxesLockoutAtConfiguredLimit, which @override_settings(AXES_ENABLED=
# True) and exercises the real /accounts/login/ endpoint (S3 test (a) / S10 /
# S6-shared). The e2e/playwright "controls active" posture (S3 test (c)) is
# owned by S3: the e2e harness exports APIS_TEST_AXES_ENABLED=1 so the
# Playwright run keeps axes active while the unit runner defaults it off.
AXES_ENABLED = eval_bool(os.getenv("APIS_TEST_AXES_ENABLED", "false"))  # noqa: F405
