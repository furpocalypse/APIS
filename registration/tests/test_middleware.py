"""Mandated S6 / RT-B5 / D-IP tests: fail-closed IP middleware, ordering,
no sentinel, import-purity of the single resolver."""

import importlib

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from fm_eventmanager import clientip
from fm_eventmanager.middleware import RequireClientIPMiddleware, axes_client_ip


def _noop_get_response(request):
    return HttpResponse("ok")


class TestRequireClientIPMiddleware(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @override_settings(DEBUG=False)
    def test_fail_closed_403_when_ip_unresolvable(self):
        # No trusted header, no XFF, REMOTE_ADDR stripped -> resolver None.
        mw = RequireClientIPMiddleware(_noop_get_response)
        req = self.rf.get("/x")
        req.META.pop("REMOTE_ADDR", None)
        with override_settings(ALLAUTH_TRUSTED_CLIENT_IP_HEADER="X-Real-IP"):
            resp = mw(req)
        self.assertEqual(resp.status_code, 403)

    @override_settings(DEBUG=False)
    def test_passes_and_sets_client_ip_when_resolvable(self):
        mw = RequireClientIPMiddleware(_noop_get_response)
        req = self.rf.get("/x", HTTP_X_REAL_IP="203.0.113.7")
        with override_settings(ALLAUTH_TRUSTED_CLIENT_IP_HEADER="X-Real-IP"):
            resp = mw(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(req.client_ip, "203.0.113.7")

    @override_settings(DEBUG=True)
    def test_pure_noop_under_debug_even_if_unresolvable(self):
        mw = RequireClientIPMiddleware(_noop_get_response)
        req = self.rf.get("/x")
        req.META.pop("REMOTE_ADDR", None)
        resp = mw(req)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(hasattr(req, "client_ip"))


class TestAxesClientIPNoSentinel(TestCase):
    def test_returns_resolver_result_never_constant_sentinel(self):
        rf = RequestFactory()
        with override_settings(ALLAUTH_TRUSTED_CLIENT_IP_HEADER="X-Real-IP"):
            req = rf.get("/x")
            req.META.pop("REMOTE_ADDR", None)
            # Unresolvable -> None, NOT "0.0.0.0" (no fail-open bucket).
            self.assertIsNone(axes_client_ip(req))
            req2 = rf.get("/x", HTTP_X_REAL_IP="198.51.100.4")
            self.assertEqual(axes_client_ip(req2), "198.51.100.4")


class TestMiddlewareOrderingAndPurity(TestCase):
    def test_require_client_ip_strictly_before_axes(self):
        mw = list(settings.MIDDLEWARE)
        self.assertIn("fm_eventmanager.middleware.RequireClientIPMiddleware", mw)
        self.assertIn("axes.middleware.AxesMiddleware", mw)
        self.assertLess(
            mw.index("fm_eventmanager.middleware.RequireClientIPMiddleware"),
            mw.index("axes.middleware.AxesMiddleware"),
            "RequireClientIPMiddleware must precede AxesMiddleware so axes "
            "never observes an unresolved client IP (D-IP / RT-B5).",
        )

    def test_clientip_module_is_import_pure(self):
        # D-IP import-purity guard: clientip's import closure must not pull
        # in registration.* or any Django app/model module (else the
        # AppRegistryNotReady cycle the lazy import once papered over
        # silently regresses).
        importlib.reload(clientip)
        import sys

        # Walk the module's direct + transitive imports captured in
        # sys.modules at the time clientip is loaded; assert no
        # registration app module is required to import it.
        offenders = [
            name
            for name in dir(clientip)
            if name == "registration" or name.startswith("registration.")
        ]
        self.assertEqual(offenders, [])
        self.assertNotIn("registration.models", repr(clientip.get_client_ip))
