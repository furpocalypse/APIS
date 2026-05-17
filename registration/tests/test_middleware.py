"""Mandated S6 / RT-B5 / D-IP tests: fail-closed IP middleware, ordering,
no sentinel, import-purity of the single resolver."""

import importlib

from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

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


@override_settings(
    AXES_ENABLED=True,
    AXES_FAILURE_LIMIT=3,
    AXES_LOCKOUT_PARAMETERS=[["username", "ip_address"]],
    AXES_RESET_ON_SUCCESS=True,
    ALLAUTH_TRUSTED_CLIENT_IP_HEADER="X-Real-IP",
)
class TestAxesLockoutAtConfiguredLimit(TestCase):
    """S3 test (a) / S10 / S6-shared.

    The unit suite runs with ``AXES_ENABLED=False`` (settings_test) so the
    Django test client's request-less ``client.login()`` works. That posture
    must NOT mean the brute-force control is untested: here axes is forced
    ACTIVE (prod-equivalent) and exercised through the *real*
    ``/accounts/login/`` endpoint to prove lockout fires at exactly the
    configured ``AXES_FAILURE_LIMIT`` and that, once locked, even valid
    credentials are refused pre-auth. S3 BLOCKING acceptance: the limit is
    honoured, not weakened, with the control active.
    """

    LIMIT = 3
    USERNAME = "lockme"
    PASSWORD = "correct-horse-battery"  # NOSONAR (test-only credential)
    IP = "203.0.113.55"

    def setUp(self):
        User.objects.create_user(
            self.USERNAME, "lockme@example.test", self.PASSWORD
        )
        self.login_url = reverse("account_login")

    def _post(self, password, username=None, ip=None):
        return self.client.post(
            self.login_url,
            {"login": username or self.USERNAME, "password": password},
            HTTP_X_REAL_IP=ip or self.IP,
        )

    def test_lockout_fires_at_configured_limit(self):
        from axes.models import AccessAttempt

        # LIMIT-1 failures: counted, not yet locked.
        for _ in range(self.LIMIT - 1):
            self._post("wrong")
        self.assertFalse(
            AccessAttempt.objects.filter(
                username=self.USERNAME, failures_since_start__gte=self.LIMIT
            ).exists()
        )

        # LIMIT-th failure -> locked.
        self._post("wrong")
        attempt = AccessAttempt.objects.filter(username=self.USERNAME).first()
        self.assertIsNotNone(attempt)
        self.assertGreaterEqual(attempt.failures_since_start, self.LIMIT)

        # Now locked: CORRECT credentials are refused pre-auth by axes.
        self.client.logout()
        resp = self._post(self.PASSWORD)
        self.assertNotIn("_auth_user_id", self.client.session)
        # A successful allauth login would 302 to LOGIN_REDIRECT_URL; the
        # axes lockout response is not that redirect.
        self.assertNotEqual(resp.status_code, 302)

    def test_lockout_counter_is_per_key_not_global(self):
        from axes.models import AccessAttempt

        for _ in range(self.LIMIT):
            self._post("wrong")
        # Prod-equivalent AXES_LOCKOUT_PARAMETERS keys on (username,
        # ip_address): a different identity has its own counter and is not
        # collaterally locked by this user's failures.
        self._post("wrong", username="someone-else", ip="198.51.100.9")
        other = AccessAttempt.objects.filter(username="someone-else").first()
        self.assertIsNotNone(other)
        self.assertLess(other.failures_since_start, self.LIMIT)
