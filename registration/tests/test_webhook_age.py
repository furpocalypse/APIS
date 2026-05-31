"""Mandated S2 tests: webhook age-window bounds + signature-bypass coupling.

Bounds are exercised here with frozen time so the rest of the suite can
run with a wide window (settings_test) against fixed historical fixtures.
"""

from django.test import RequestFactory, TestCase, override_settings
from freezegun import freeze_time

from registration.views.webhook_age import (
    webhook_signature_bypassed,
    webhook_within_age_window,
)

NOW = "2026-05-17T12:00:00Z"


@override_settings(WEBHOOK_MAX_AGE_SECONDS=3600, WEBHOOK_FUTURE_SKEW_SECONDS=300)
class TestWebhookAgeWindow(TestCase):
    @freeze_time(NOW)
    def test_in_window(self):
        self.assertTrue(webhook_within_age_window("2026-05-17T11:30:00Z", source="t"))

    @freeze_time(NOW)
    def test_too_old(self):
        self.assertFalse(webhook_within_age_window("2026-05-17T10:00:00Z", source="t"))

    @freeze_time(NOW)
    def test_too_far_future(self):
        self.assertFalse(webhook_within_age_window("2026-05-17T12:30:00Z", source="t"))

    @freeze_time(NOW)
    def test_just_inside_bounds(self):
        self.assertTrue(webhook_within_age_window("2026-05-17T11:00:01Z", source="t"))
        self.assertTrue(webhook_within_age_window("2026-05-17T12:04:59Z", source="t"))

    @freeze_time(NOW)
    def test_none_empty_garbage_reject(self):
        for bad in (None, "", "not-a-timestamp", "2026-13-99T99:99:99Z"):
            self.assertFalse(webhook_within_age_window(bad, source="t"))

    @freeze_time(NOW)
    def test_trailing_z_and_offset_forms(self):
        self.assertTrue(webhook_within_age_window("2026-05-17T11:30:00Z", source="t"))
        self.assertTrue(webhook_within_age_window("2026-05-17T11:30:00+00:00", source="t"))

    @freeze_time(NOW)
    def test_naive_timestamp_assumed_utc(self):
        self.assertTrue(webhook_within_age_window("2026-05-17T11:30:00", source="t"))


class TestWebhookSignatureBypassed(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @override_settings(E2E_MODE=True)
    def test_bypassed_when_e2e_mode_and_header(self):
        req = self.rf.post("/", HTTP_X_E2E_MOCK_SIGNATURE="1")
        self.assertTrue(webhook_signature_bypassed(req))

    @override_settings(E2E_MODE=True)
    def test_not_bypassed_without_header(self):
        self.assertFalse(webhook_signature_bypassed(self.rf.post("/")))

    @override_settings(E2E_MODE=False)
    def test_not_bypassed_when_mode_off_even_with_header(self):
        # Prod-safety (S2b): with E2E_MODE False the mock header is inert.
        req = self.rf.post("/", HTTP_X_E2E_MOCK_SIGNATURE="1")
        self.assertFalse(webhook_signature_bypassed(req))
