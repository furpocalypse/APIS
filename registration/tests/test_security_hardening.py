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
    TRUSTED_PROXY_PLACEHOLDER,
    assert_no_placeholder_proxy_cidrs,
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


class TestProxyCidrPlaceholderGuard(SimpleTestCase):
    """Peer-review: prod must fail loud on the unmodified placeholder,
    but an intentionally-empty list (T1 nginx origin-lock) is allowed."""

    def test_placeholder_rejected(self):
        with self.assertRaises(RuntimeError):
            assert_no_placeholder_proxy_cidrs([TRUSTED_PROXY_PLACEHOLDER])

    def test_placeholder_among_others_rejected(self):
        with self.assertRaises(RuntimeError):
            assert_no_placeholder_proxy_cidrs(["10.1.2.0/24", TRUSTED_PROXY_PLACEHOLDER])

    def test_empty_allowed(self):
        assert_no_placeholder_proxy_cidrs([])  # legitimate T1 posture
        assert_no_placeholder_proxy_cidrs(None)

    def test_real_cidr_allowed(self):
        assert_no_placeholder_proxy_cidrs(["10.10.0.0/24", "172.16.4.0/22"])


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

    # Peer-review Blue Team F1/F2/F3: the client IP ("where") and event
    # timestamp ("when") of a security audit line must survive redaction
    # while co-located true PII is still masked.
    def test_ipv4_preserved_in_med13_reject_line(self):
        line = (
            "RequireClientIPMiddleware: rejecting request whose peer is not "
            "in TRUSTED_PROXY_CIDRS. path=/x host=h remote_addr='203.0.113.45' "
            "xff='198.51.100.23, evil@example.com'"
        )
        out = redact(line)
        self.assertIn("203.0.113.45", out)
        self.assertIn("198.51.100.23", out)
        self.assertIn("[redacted-email]", out)  # co-located PII still masked
        self.assertNotIn("evil@example.com", out)

    def test_iso_timestamp_preserved_in_webhook_age_line(self):
        line = (
            "PayPal webhook timestamp 2026-05-17T00:00:00.123456+00:00 is "
            "4500s old; contact billing@example.org"
        )
        out = redact(line)
        self.assertIn("2026-05-17T00:00:00.123456+00:00", out)
        self.assertIn("[redacted-email]", out)
        self.assertNotIn("billing@example.org", out)

    def test_filter_keeps_ip_masks_pan_same_line(self):
        record = self._record(
            "peer 198.51.100.234 sent card 4111 1111 1111 1111",
        )
        self.filter.filter(record)
        msg = record.getMessage()
        # 198.51.100.234 has a 12-digit run that _PHONE_RE would have
        # eaten pre-FIX-C — non-vacuous IP-survival assertion.
        self.assertIn("198.51.100.234", msg)
        self.assertIn("[redacted-pan]", msg)

    def test_ip_literal_email_domain_still_redacted(self):
        # Blue Team F4: an email whose domain is an IPv4 literal must NOT
        # escape redaction just because the IPv4-span carve-out protects
        # bare audit IPs. Email is redacted over the full text first.
        for addr in ("admin@192.168.1.1", "joe@10.0.0.5", "x@127.0.0.1"):
            out = redact(f"login from {addr} failed")
            self.assertNotIn(addr, out, f"{addr} leaked")
            self.assertIn("[redacted-email]", out)
        # A bare (non-email) audit IP is still preserved alongside.
        out = redact("peer 203.0.113.45 user bob@example.com")
        self.assertIn("203.0.113.45", out)
        self.assertNotIn("bob@example.com", out)
