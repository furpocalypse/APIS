"""Regression tests for the PayPal webhook total-failure incident.

The production outage: ``PAYPAL_WEBHOOK_ID`` was supplied by the deployment
environment but never mapped into Django settings, so ``verify_signature``
failed closed and 403'd 100% of deliveries — while the unit suite stayed
green because every test injected the setting via ``@override_settings``.

These tests close the structural gaps that let that happen:

* an env→settings contract test that runs the REAL settings module in a
  subprocess (no ``override_settings`` masking);
* a full-stack smoke test that POSTs through the real URLconf, middleware,
  decorators, and the real ``verify_signature`` (only ``urlopen`` mocked);
* distinguishing-log assertions for the previously silent verify branches;
* the production-default freshness window exercised through
  ``verify_signature`` (the suite-wide ``.env.test`` window is ~100 years);
* the boot-time PayPal config completeness check;
* the admin retry action dispatching PayPal rows to the PayPal handler;
* the checkout SDK environment parser that crashed on an empty string.
"""

import base64
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
    tag,
)
from django.urls import reverse
from django.utils import timezone
from paypalserversdk.configuration import Environment

from fm_eventmanager.security_checks import assert_complete_paypal_config
from registration.admin import process_unprocessed_notifications
from registration.models import PaymentWebhookNotification
from registration.paypal_payments import _sdk_environment
from registration.views.paypal_webhooks import verify_signature

REPO_ROOT = Path(__file__).resolve().parents[2]

# Three days — the production default for WEBHOOK_MAX_AGE_SECONDS
# (settings.py sizes it to PayPal's ~3-day retry horizon).
PRODUCTION_MAX_AGE = 60 * 60 * 24 * 3
PRODUCTION_FUTURE_SKEW = 300


def _mock_response(json_body, status=200):
    """A MagicMock usable as an ``urllib.request.urlopen`` return value
    (context-manager protocol, ``read()`` + ``status``). Mirrors the helper
    in test_paypal_webhooks.TestPaypalWebhookSignatureVerification."""
    inner = MagicMock()
    inner.read.return_value = json.dumps(json_body).encode("utf-8")
    inner.status = status
    cm = MagicMock()
    cm.__enter__.return_value = inner
    cm.__exit__.return_value = False
    return cm


def _paypal_headers(transmission_time=None):
    """The five required paypal-* headers, with a FRESH transmission time by
    default (the fixture timestamps elsewhere in the suite only pass because
    .env.test disables the age window)."""
    if transmission_time is None:
        transmission_time = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.paypal.com/v1/notifications/certs/CERT-ID",
        "paypal-transmission-id": "TRANS-ID-REGRESSION",
        "paypal-transmission-sig": "SIG",
        "paypal-transmission-time": transmission_time,
    }


def _paypal_event_body(event_id="WH-REGRESSION-0001", event_type="TEST.SMOKE.EVENT"):
    return {
        "id": event_id,
        "event_version": "1.0",
        "create_time": timezone.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "resource_type": "capture",
        "event_type": event_type,
        "summary": "Regression-test event",
        "resource": {"id": "CAPTURE-1", "status": "COMPLETED"},
    }


# Env for booting the real settings module as a production process in a
# subprocess: satisfies every _IS_PROD / DEBUG=False fail-closed gate in
# settings.py that runs BEFORE the PayPal completeness check, so subprocess
# tests reach (and can assert on) the check itself.
PROD_BOOT_ENV = {
    "DJANGO_SETTINGS_MODULE": "fm_eventmanager.settings",
    "APIS_ENV": "production",
    "DJANGO_DEBUG": "False",
    "E2E_MODE": "False",
    "ALLOWED_HOSTS": "example.com",
    "CSRF_TRUSTED_ORIGINS": "https://example.com",
    "TRUSTED_CLIENT_IP_HEADER": "X-Forwarded-For",
    "MQTT_JWT_SECRET": base64.b64encode(b"\x42" * 32).decode("ascii"),
    "TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
    "APIS_METRICS_BACKEND": "DummyReporter",
}


def _boot_settings_subprocess(extra_env, code="import django; django.setup()"):
    env = dict(os.environ)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=120,
    )


@tag("PayPal")
class TestPaypalSettingsEnvContract(SimpleTestCase):
    """The env→settings contract whose absence caused the outage.

    ``override_settings`` happily creates attributes the real settings
    module never defines, so only a test against the REAL module can catch
    a missing ``os.getenv`` mapping.
    """

    def test_webhook_id_defined_by_real_settings_module(self):
        # No override_settings anywhere in this class: this reads whatever
        # the actual settings module defined at import time.
        from django.conf import settings

        self.assertTrue(
            hasattr(settings, "PAYPAL_WEBHOOK_ID"),
            "fm_eventmanager.settings must define PAYPAL_WEBHOOK_ID "
            "(env-mapped); verify_signature fails closed without it and "
            "every production webhook delivery is rejected 403.",
        )

    def test_paypal_env_vars_reach_settings(self):
        """Import the real settings module in a subprocess with the env vars
        set and assert the values arrive. This is the test that would have
        caught the incident: the env var was set in production, but
        settings.py never read it."""
        sentinel = "CONTRACT-TEST-WEBHOOK-ID"
        env = dict(os.environ)
        env["DJANGO_SETTINGS_MODULE"] = "fm_eventmanager.settings"
        env["PAYPAL_WEBHOOK_ID"] = sentinel
        env["PAYPAL_CLIENT_ID"] = "contract-cid"
        env["PAYPAL_CLIENT_SECRET"] = "contract-secret"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import django; django.setup(); "
                "from django.conf import settings; "
                "print(settings.PAYPAL_WEBHOOK_ID); "
                "print(settings.PAYPAL_CLIENT_ID); "
                "print(settings.PAYPAL_CLIENT_SECRET)",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO_ROOT,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()[-3:]
        self.assertEqual(lines, [sentinel, "contract-cid", "contract-secret"])

    def test_webhook_logger_stanzas_pinned_to_info(self):
        """The INFO logger entries for the webhook modules are what keeps
        the success-path log visible in production (the parent
        `registration.views` logger is pinned to WARNING). assertLogs
        bypasses LOGGING entirely, so only this settings-contract assertion
        catches the stanza being reverted."""
        from django.conf import settings

        for name in (
            "registration.views.paypal_webhooks",
            "registration.views.webhooks",
        ):
            stanza = settings.LOGGING["loggers"].get(name)
            self.assertIsNotNone(stanza, f"LOGGING must pin logger {name}")
            self.assertEqual(stanza["level"], "INFO", name)
            self.assertIn("console", stanza["handlers"], name)

    def test_console_handler_clamped_to_info_or_debug(self):
        """DJANGO_LOGLEVEL may only LOWER the console threshold. A raised
        threshold would silently drop the pinned security WARNINGs and the
        webhook INFO lines — recreating the silent-failure blindness this
        changeset fixes."""
        code = (
            "import django; django.setup(); "
            "from django.conf import settings; "
            'print(settings.LOGGING["handlers"]["console"]["level"])'
        )
        for loglevel, expected in (
            ("debug", "DEBUG"),
            ("info", "INFO"),
            ("warning", "INFO"),  # clamped: must not raise the threshold
            ("bogus", "INFO"),
        ):
            with self.subTest(loglevel=loglevel):
                result = _boot_settings_subprocess({"DJANGO_LOGLEVEL": loglevel}, code=code)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[-1], expected)

    def test_production_boot_refuses_partial_paypal_config(self):
        """The incident shape, end-to-end through the real settings module:
        APIS_ENV=production with credentials set but PAYPAL_WEBHOOK_ID
        empty must refuse to boot. This exercises the settings.py wiring of
        assert_complete_paypal_config, not just the helper."""
        result = _boot_settings_subprocess(
            {
                **PROD_BOOT_ENV,
                "PAYPAL_CLIENT_ID": "cid",
                "PAYPAL_CLIENT_SECRET": "sec",
                "PAYPAL_WEBHOOK_ID": "",
                "PAYPAL_ENVIRONMENT": "production",
            }
        )
        self.assertNotEqual(result.returncode, 0)
        # Assert the SPECIFIC message so a failure from some earlier boot
        # gate cannot make this test pass for the wrong reason.
        self.assertIn("Partial PayPal configuration", result.stderr)
        self.assertIn("PAYPAL_WEBHOOK_ID", result.stderr)

    def test_production_boot_accepts_complete_paypal_config(self):
        result = _boot_settings_subprocess(
            {
                **PROD_BOOT_ENV,
                "PAYPAL_CLIENT_ID": "cid",
                "PAYPAL_CLIENT_SECRET": "sec",
                "PAYPAL_WEBHOOK_ID": "wh-id",
                "PAYPAL_ENVIRONMENT": "production",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)


@tag("PayPal")
@override_settings(
    PAYPAL_WEBHOOK_ID="TEST-WEBHOOK-ID",
    PAYPAL_CLIENT_ID="CID",
    PAYPAL_CLIENT_SECRET="SEC",
)
class TestPaypalWebhookFullStackSmoke(TestCase):
    """POST a canned PayPal event through the real URLconf, middleware,
    decorator stack, and the REAL ``verify_signature`` — only the outbound
    ``urlopen`` calls are mocked. A wiring regression anywhere on the
    inbound path (routing, csrf/require_POST/ratelimit composition,
    settings gating, freshness) fails this test even when every other test
    in the suite passes."""

    def setUp(self):
        # Clear before AND after: the real verify path caches the OAuth
        # token in the shared Redis cache, and later tests in the suite
        # (e.g. TestPaypalViewsCoverageGaps) assume no token is cached.
        cache.clear()
        self.addCleanup(cache.clear)

    @override_settings(
        WEBHOOK_MAX_AGE_SECONDS=PRODUCTION_MAX_AGE,
        WEBHOOK_FUTURE_SKEW_SECONDS=PRODUCTION_FUTURE_SKEW,
    )
    @patch("urllib.request.urlopen")
    def test_fresh_event_accepted_end_to_end(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"access_token": "tok", "expires_in": 32400}),
            _mock_response({"verification_status": "SUCCESS"}),
        ]
        body = _paypal_event_body()
        with self.assertLogs("registration.views.paypal_webhooks", level="INFO") as captured:
            response = self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers=_paypal_headers(),
            )
        self.assertEqual(response.status_code, 200)
        notification = PaymentWebhookNotification.objects.get(
            integration="paypal", event_id=body["id"]
        )
        # Unknown event type: stored but unprocessed — the smoke test's
        # subject is the inbound path, not a specific handler.
        self.assertFalse(notification.processed)
        # The success-path log line must be emitted (a healthy pipeline
        # must be distinguishable from a silent one in production logs).
        self.assertTrue(
            any("stored" in message for message in captured.output),
            captured.output,
        )

    @override_settings(
        WEBHOOK_MAX_AGE_SECONDS=PRODUCTION_MAX_AGE,
        WEBHOOK_FUTURE_SKEW_SECONDS=PRODUCTION_FUTURE_SKEW,
    )
    @patch("urllib.request.urlopen")
    def test_stale_event_rejected_under_production_window(self, mock_urlopen):
        """A 4-day-old transmission time must 403 under the production
        window. The rest of the suite runs with the window disabled
        (.env.test sets it to ~100 years), so only this asserts the
        deployed freshness behavior through the full stack."""
        mock_urlopen.side_effect = [
            _mock_response({"access_token": "tok", "expires_in": 32400}),
            _mock_response({"verification_status": "SUCCESS"}),
        ]
        stale = (timezone.now() - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with self.assertLogs("registration.views", level="WARNING") as captured:
            response = self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(_paypal_event_body(event_id="WH-REGRESSION-STALE")),
                content_type="application/json",
                headers=_paypal_headers(transmission_time=stale),
            )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PaymentWebhookNotification.objects.filter(event_id="WH-REGRESSION-STALE").exists()
        )
        # The age-window reject must be attributable in logs.
        self.assertTrue(
            any("webhook[paypal]" in message for message in captured.output),
            captured.output,
        )


@tag("PayPal")
@override_settings(
    PAYPAL_WEBHOOK_ID="TEST-WEBHOOK-ID",
    PAYPAL_CLIENT_ID="CID",
    PAYPAL_CLIENT_SECRET="SEC",
)
class TestPaypalVerifyBranchLogging(TestCase):
    """Every fail-closed branch of verify_signature must emit a
    distinguishing log line. The two asserted here were previously silent —
    and one of them (verification_status != SUCCESS) is exactly the branch
    a wrong-webhook-ID / wrong-app / sandbox-vs-live misconfiguration
    exercises on 100% of deliveries."""

    def setUp(self):
        # See TestPaypalWebhookFullStackSmoke.setUp: never leak a cached
        # OAuth token into later tests via the shared Redis cache.
        cache.clear()
        self.addCleanup(cache.clear)

    def _request(self):
        factory = RequestFactory()
        meta = {
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in _paypal_headers().items()
        }
        return factory.post(
            "/registration/webhook/paypal/v1",
            data=json.dumps(_paypal_event_body()),
            content_type="application/json",
            **meta,
        )

    @patch("urllib.request.urlopen")
    def test_verification_failure_status_is_logged(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"access_token": "tok", "expires_in": 32400}),
            _mock_response({"verification_status": "FAILURE"}),
        ]
        with self.assertLogs("registration.views.paypal_webhooks", level="ERROR") as captured:
            self.assertFalse(verify_signature(self._request()))
        self.assertTrue(
            any("verification_status" in message for message in captured.output),
            captured.output,
        )

    @patch("urllib.request.urlopen")
    def test_oauth_response_without_token_is_logged(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_response({"token_type": "Bearer"}),  # 200, no access_token
        ]
        with self.assertLogs("registration.views.paypal_webhooks", level="ERROR") as captured:
            self.assertFalse(verify_signature(self._request()))
        self.assertTrue(
            any("access_token" in message for message in captured.output),
            captured.output,
        )

    @patch("urllib.request.urlopen")
    def test_verify_transport_anomaly_not_blamed_on_config(self, mock_urlopen):
        """A non-200 verify response yields no verification verdict; the log
        must report a transport/API anomaly, not steer the operator toward
        PAYPAL_WEBHOOK_ID configuration."""
        mock_urlopen.side_effect = [
            _mock_response({"access_token": "tok", "expires_in": 32400}),
            _mock_response({"verification_status": "SUCCESS"}, status=500),
        ]
        with self.assertLogs("registration.views.paypal_webhooks", level="ERROR") as captured:
            self.assertFalse(verify_signature(self._request()))
        self.assertTrue(
            any("no parseable verification response" in message for message in captured.output),
            captured.output,
        )
        self.assertFalse(
            any("verification_status=" in message for message in captured.output),
            captured.output,
        )


@tag("PayPal")
class TestPaypalConfigBootCheck(SimpleTestCase):
    """assert_complete_paypal_config: all-or-nothing in production."""

    def test_all_empty_allowed(self):
        assert_complete_paypal_config(
            client_id="", client_secret="", webhook_id="", environment="production"
        )

    def test_none_values_treated_as_empty(self):
        assert_complete_paypal_config(
            client_id=None, client_secret=None, webhook_id=None, environment=None
        )

    def test_fully_configured_allowed(self):
        for environment in ("production", "sandbox", "PRODUCTION", "Sandbox"):
            assert_complete_paypal_config(
                client_id="cid",
                client_secret="sec",
                webhook_id="wh",
                environment=environment,
            )

    def test_missing_webhook_id_refused(self):
        """The incident shape: credentials present, webhook ID absent."""
        with self.assertRaisesMessage(RuntimeError, "PAYPAL_WEBHOOK_ID"):
            assert_complete_paypal_config(
                client_id="cid",
                client_secret="sec",
                webhook_id="",
                environment="production",
            )

    def test_missing_credentials_refused(self):
        with self.assertRaisesMessage(RuntimeError, "PAYPAL_CLIENT_SECRET"):
            assert_complete_paypal_config(
                client_id="cid",
                client_secret="",
                webhook_id="wh",
                environment="production",
            )

    def test_unrecognized_environment_refused_when_configured(self):
        for environment in ("", "live", "prod-eu", None):
            with self.assertRaisesMessage(RuntimeError, "PAYPAL_ENVIRONMENT"):
                assert_complete_paypal_config(
                    client_id="cid",
                    client_secret="sec",
                    webhook_id="wh",
                    environment=environment,
                )

    def test_environment_not_validated_when_paypal_disabled(self):
        assert_complete_paypal_config(
            client_id="", client_secret="", webhook_id="", environment="anything"
        )


@tag("PayPal")
class TestAdminWebhookRetryDispatch(TestCase):
    """The admin 'Process unprocessed actions' action must dispatch each
    notification to its own integration's processor. Previously every row
    went to the Square dispatcher, which KeyErrors on PayPal bodies (no
    'type' key) — making the only manual retry path crash."""

    def _make(self, integration, body):
        return PaymentWebhookNotification.objects.create(
            integration=integration,
            event_id=f"EVT-{integration}",
            event_type="",
            body=body,
            headers={},
        )

    def test_paypal_rows_route_to_paypal_processor(self):
        notification = self._make("paypal", _paypal_event_body())
        with (
            patch("registration.views.paypal_webhooks.process_webhook") as paypal_mock,
            patch("registration.views.webhooks.process_webhook") as square_mock,
        ):
            process_unprocessed_notifications(None, None, PaymentWebhookNotification.objects.all())
        paypal_mock.assert_called_once()
        self.assertEqual(paypal_mock.call_args[0][0].pk, notification.pk)
        square_mock.assert_not_called()

    def test_square_rows_still_route_to_square_processor(self):
        notification = self._make("square", {"type": "refund.updated"})
        with (
            patch("registration.views.paypal_webhooks.process_webhook") as paypal_mock,
            patch("registration.views.webhooks.process_webhook") as square_mock,
        ):
            process_unprocessed_notifications(None, None, PaymentWebhookNotification.objects.all())
        square_mock.assert_called_once()
        self.assertEqual(square_mock.call_args[0][0].pk, notification.pk)
        paypal_mock.assert_not_called()

    def test_paypal_row_reprocesses_without_crashing(self):
        """End-to-end through the REAL PayPal processor: a PayPal body has
        no 'type' key, so the pre-fix dispatch died with KeyError before
        reaching any handler."""
        notification = self._make("paypal", _paypal_event_body(event_type="TEST.NO.HANDLER"))
        process_unprocessed_notifications(None, None, PaymentWebhookNotification.objects.all())
        notification.refresh_from_db()
        # Unknown event type: still unprocessed, but no exception raised.
        self.assertFalse(notification.processed)


@tag("PayPal")
class TestPaypalSdkEnvironmentParsing(SimpleTestCase):
    """_sdk_environment must never crash and must agree with the webhook
    path's prefix rule (previously ``.lower()[0]`` raised IndexError on an
    empty PAYPAL_ENVIRONMENT — at import time, taking the whole process
    down)."""

    def test_empty_and_none_select_sandbox_without_crashing(self):
        self.assertEqual(_sdk_environment(""), Environment.SANDBOX)
        self.assertEqual(_sdk_environment(None), Environment.SANDBOX)

    def test_production_values(self):
        for value in ("production", "PRODUCTION", "Production", "p"):
            self.assertEqual(_sdk_environment(value), Environment.PRODUCTION)

    def test_sandbox_values(self):
        for value in ("sandbox", "SANDBOX", "live", "anything-else"):
            self.assertEqual(_sdk_environment(value), Environment.SANDBOX)

    def test_agrees_with_webhook_api_base_selection(self):
        """The checkout SDK and the webhook verify path must select live vs
        sandbox identically for every input, or checkout could charge live
        while webhook verification silently talks to sandbox."""
        from registration.views.paypal_webhooks import (
            PAYPAL_LIVE_API_BASE,
            _paypal_api_base,
        )

        for value in ("", "p", "production", "PRODUCTION", "sandbox", "live", "prod-eu"):
            with self.subTest(value=value), override_settings(PAYPAL_ENVIRONMENT=value):
                self.assertEqual(
                    _sdk_environment(value) == Environment.PRODUCTION,
                    _paypal_api_base() == PAYPAL_LIVE_API_BASE,
                )


@tag("PayPal")
# RESPONSE_TYPE=json keeps the 503 from rendering 503.html, which needs a
# built staticfiles manifest the unit-test environment doesn't have.
@override_settings(MAINTENANCE_MODE=True, MAINTENANCE_MODE_RESPONSE_TYPE="json")
class TestMaintenanceModeWebhookExemption(TestCase):
    """Maintenance mode must not 503 payment-provider webhooks: providers
    mark the deliveries failed and events older than the retry horizon are
    lost to automatic delivery."""

    def test_maintenance_mode_is_actually_on(self):
        # Control: a non-exempt URL gets the 503, proving the override
        # engaged (otherwise the webhook assertions below prove nothing).
        response = self.client.get("/")
        self.assertEqual(response.status_code, 503)

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_paypal_webhook_served_during_maintenance(self, mock_verify):
        mock_verify.return_value = True
        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(_paypal_event_body(event_id="WH-MAINT-1")),
            content_type="application/json",
            headers=_paypal_headers(),
        )
        self.assertEqual(response.status_code, 200)

    def test_square_webhook_not_503_during_maintenance(self):
        # Signature fails (403) — the point is it reached the view instead
        # of being short-circuited by the maintenance middleware's 503.
        response = self.client.post(
            reverse("registration:square_webhook"),
            json.dumps({"event_id": "sq-1", "type": "refund.updated"}),
            content_type="application/json",
            headers={"x-square-hmacsha256-signature": "bogus"},
        )
        # 403 (signature rejection) proves the request traversed the
        # maintenance middleware and reached the view; assertNotEqual(503)
        # would also pass on 404/500 wiring breakage.
        self.assertEqual(response.status_code, 403)
