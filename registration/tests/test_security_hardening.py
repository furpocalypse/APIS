"""Locking tests for SECURITY_REVIEW_2026-05-11 MED-5 and MED-6.

MED-5: production must reject a missing / non-base64 / under-strength
MQTT JWT secret. MED-6: the central logging filter must mask email / PAN /
phone PII before a record reaches a handler.
"""

import base64
import logging

from django.test import SimpleTestCase

from fm_eventmanager.log_redaction import PIIRedactingFilter, redact
from fm_eventmanager.security_checks import (
    MQTT_MIN_KEY_BYTES,
    assert_strong_mqtt_secret,
)


class TestMqttSecretStrength(SimpleTestCase):
    """MED-5 — assert_strong_mqtt_secret is the production boot guard."""

    def test_none_rejected(self):
        with self.assertRaises(RuntimeError):
            assert_strong_mqtt_secret(None)

    def test_empty_rejected(self):
        with self.assertRaises(RuntimeError):
            assert_strong_mqtt_secret("")

    def test_non_base64_rejected(self):
        with self.assertRaises(RuntimeError):
            assert_strong_mqtt_secret("not valid base64 !!!")

    def test_short_key_rejected(self):
        # 16 decoded bytes — below the HMAC-SHA256 floor.
        short = base64.b64encode(b"x" * 16).decode()
        with self.assertRaises(RuntimeError):
            assert_strong_mqtt_secret(short)

    def test_boundary_just_below_rejected(self):
        almost = base64.b64encode(b"x" * (MQTT_MIN_KEY_BYTES - 1)).decode()
        with self.assertRaises(RuntimeError):
            assert_strong_mqtt_secret(almost)

    def test_strong_key_accepted(self):
        strong = base64.b64encode(b"x" * MQTT_MIN_KEY_BYTES).decode()
        # Must not raise.
        assert_strong_mqtt_secret(strong)


class TestPIIRedaction(SimpleTestCase):
    """MED-6 — PIIRedactingFilter masks PII in rendered log messages."""

    def setUp(self):
        self.filter = PIIRedactingFilter()

    def _record(self, msg, *args):
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_redact_email(self):
        self.assertEqual(
            redact("contact jane.doe+tag@example.co.uk now"),
            "contact [redacted-email] now",
        )

    def test_redact_pan(self):
        self.assertIn("[redacted-pan]", redact("card 4111 1111 1111 1111 ok"))

    def test_redact_phone(self):
        self.assertIn("[redacted-phone]", redact("call +1 (415) 555-2671 asap"))

    def test_non_pii_untouched(self):
        clean = "order TEST-42 status Completed in 12 ms"
        self.assertEqual(redact(clean), clean)

    def test_filter_rewrites_record_and_clears_args(self):
        record = self._record("user %s logged in", "bob@example.com")
        result = self.filter.filter(record)
        self.assertTrue(result)  # filter never suppresses a line
        self.assertEqual(record.getMessage(), "user [redacted-email] logged in")
        self.assertEqual(record.args, ())

    def test_filter_fails_open_on_bad_record(self):
        # A record whose %-formatting would raise must still pass through
        # unredacted rather than the filter throwing.
        record = self._record("bad %d format", "not-an-int")
        self.assertTrue(self.filter.filter(record))
