"""Locking tests for SECURITY_REVIEW MED-3 / MED-4 (Decision #12a).

Exercises the ``rate_limited_json`` wrapper directly (fast, no DB) and
asserts the submit/webhook endpoints are actually wrapped.
"""

import json
from unittest.mock import patch

from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from registration.ratelimit import rate_limited_json

# LocMem so the limiter is deterministic without a live Redis.
_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=_LOCMEM, RATELIMIT_ENABLE=True)
class TestRateLimitedJson(SimpleTestCase):
    def setUp(self):
        from django.core.cache import caches

        caches["default"].clear()
        self.rf = RequestFactory()

    def _view(self, rate):
        @rate_limited_json(rate=rate, group="test.group")
        def view(request):
            return JsonResponse({"success": True})

        return view

    def test_allows_under_limit(self):
        view = self._view("3/m")
        for _ in range(3):
            resp = view(self.rf.post("/x", REMOTE_ADDR="203.0.113.7"))
            self.assertEqual(resp.status_code, 200)

    def test_blocks_over_limit_with_429_json(self):
        view = self._view("2/m")
        for _ in range(2):
            self.assertEqual(view(self.rf.post("/x", REMOTE_ADDR="203.0.113.8")).status_code, 200)
        resp = view(self.rf.post("/x", REMOTE_ADDR="203.0.113.8"))
        self.assertEqual(resp.status_code, 429)
        body = json.loads(resp.content)
        self.assertFalse(body["success"])
        self.assertEqual(body["reason"], "Rate limit exceeded")

    def test_buckets_are_per_ip(self):
        view = self._view("1/m")
        self.assertEqual(view(self.rf.post("/x", REMOTE_ADDR="198.51.100.1")).status_code, 200)
        # Different source IP is an independent bucket.
        self.assertEqual(view(self.rf.post("/x", REMOTE_ADDR="198.51.100.2")).status_code, 200)
        # Same first IP again is now over its 1/m limit.
        self.assertEqual(view(self.rf.post("/x", REMOTE_ADDR="198.51.100.1")).status_code, 429)

    def test_fails_open_when_backend_errors(self):
        view = self._view("1/m")
        with patch("registration.ratelimit.is_ratelimited", side_effect=RuntimeError("redis down")):
            resp = view(self.rf.post("/x", REMOTE_ADDR="203.0.113.9"))
        # Abuse control must never take down a legitimate flow.
        self.assertEqual(resp.status_code, 200)


class TestEndpointsAreWrapped(SimpleTestCase):
    """Introspection: the MED-3/MED-4 surfaces carry the limiter."""

    def _is_wrapped(self, func) -> bool:
        # rate_limited_json uses functools.wraps; the wrapper closure binds
        # the limiter. Walk __wrapped__ and confirm the module-qualified
        # source is registration.ratelimit for one frame in the chain.
        seen = set()
        f = func
        while f is not None and id(f) not in seen:
            seen.add(id(f))
            code = getattr(f, "__code__", None)
            if code and code.co_filename.endswith("registration/ratelimit.py"):
                return True
            f = getattr(f, "__wrapped__", None)
        return False

    def test_submit_and_webhook_views_wrapped(self):
        from registration.views import dealers, ordering, paypal_webhooks, staff, webhooks

        for view in (
            ordering.checkout,
            dealers.add_dealer,
            dealers.addNewDealer,
            staff.add_new_staff,
            staff.add_returning_staff,
            webhooks.square_webhook,
            paypal_webhooks.paypal_webhook,
        ):
            self.assertTrue(self._is_wrapped(view), f"{view!r} not rate-limited")
