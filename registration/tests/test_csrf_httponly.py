"""Regression coverage for the CSRF_COOKIE_HTTPONLY legacy-form breakage.

Background
----------
The OWASP-hardening work set ``CSRF_COOKIE_HTTPONLY = True``. An HttpOnly
csrftoken cookie is still sent to the server but is invisible to
``document.cookie``, so the legacy jQuery forms could no longer read the
token to put it in the ``X-CSRFToken`` header. Every legacy AJAX POST
(registration submit, checkout, dealer, staff, ...) started returning
HTTP 403 "CSRF verification failed".

The fix renders the token into ``<meta name="csrf_token">`` in
``master.html`` and points ``main.js`` / ``dealer-form.html`` at that tag
instead of the cookie (mirroring what the SPA already does).

This test pins the *server-side* contract the patched client depends on:

1. The token is rendered into the meta tag on a master.html page.
2. The csrftoken cookie really is HttpOnly (i.e. the cookie path is
   genuinely unavailable to JS — this is *why* the meta tag is required;
   if HttpOnly were ever dropped this assertion flags the config drift).
3. A CSRF-protected legacy endpoint REJECTS a POST with no token
   (negative control — reproduces the reported bug, proves enforcement
   is actually on so #4 is meaningful).
4. The same POST with the meta-tag token in ``X-CSRFToken`` is ACCEPTED
   (exactly what the patched ``main.js`` does).

``settings_test.py`` already sets ``CSRF_COOKIE_HTTPONLY = True``, so the
test environment reproduces the production condition without overrides.
"""

import json
import re

from django.test import Client, override_settings
from django.urls import reverse

from registration.tests.common import *

META_CSRF_RE = re.compile(r'<meta name="csrf_token" content="([^"]+)">')


# Pin the environment so this test isolates the *CSRF token source*
# (HttpOnly cookie vs meta tag) and nothing else:
#
#  - STORAGES: the legacy form path (master.html → registration-form.html)
#    uses only {% static %}, no Vite tags. Use the plain (non-manifest)
#    static storage so the test doesn't need a frontend build/collectstatic.
#  - SECURE_SSL_REDIRECT=False: settings_test turns this on when DEBUG is
#    False, which would 301 the http test client before CSRF runs.
#  - *_COOKIE_SECURE=False: keep the http test client's cookie round-trip
#    unambiguous.
#  - CSRF_COOKIE_HTTPONLY=True is asserted in-test and left as-is — it is
#    the production bug condition this whole fix exists for.
@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
    SECURE_SSL_REDIRECT=False,
    CSRF_COOKIE_SECURE=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_HTTPONLY=True,
)
class CsrfHttpOnlyMetaTokenRegression(OrdersTestCase):
    def test_legacy_form_post_uses_meta_token_not_cookie(self):
        # enforce_csrf_checks=True makes the test client run the real
        # CsrfViewMiddleware (it is otherwise bypassed in tests).
        client = Client(enforce_csrf_checks=True)

        # 1. The registration page extends master.html and must render the
        #    token into the meta tag the patched main.js reads.
        page = client.get(reverse("registration:index"))
        self.assertEqual(page.status_code, 200)
        match = META_CSRF_RE.search(page.content.decode())
        self.assertIsNotNone(
            match,
            'master.html did not render <meta name="csrf_token" ...>; '
            "the legacy jQuery forms have no way to obtain the token.",
        )
        token = match.group(1)
        self.assertTrue(token, "csrf_token meta tag rendered empty")

        # 2. The cookie must be HttpOnly. This is the bug condition: it is
        #    precisely because JS cannot read this cookie that the meta tag
        #    is mandatory. If this ever flips, the fix is moot — fail loud.
        csrf_cookie = page.cookies.get("csrftoken")
        self.assertIsNotNone(csrf_cookie, "csrftoken cookie was not set")
        self.assertTrue(
            csrf_cookie["httponly"],
            "csrftoken cookie is not HttpOnly — the meta-tag workaround "
            "assumes it is; investigate settings drift.",
        )

        payload = json.dumps(
            {
                "attendee": self.attendee_form_2,
                "priceLevel": {"id": self.price_45.id, "options": []},
                "event": self.event.name,
            }
        )

        # 3. Negative control: no X-CSRFToken header → this is exactly the
        #    pre-fix behaviour (cookie unreadable ⇒ no/empty header). Must
        #    be rejected, which also proves CSRF enforcement is live here.
        denied = client.post(
            reverse("registration:add_to_cart"),
            payload,
            content_type="application/json",
        )
        self.assertEqual(
            denied.status_code,
            403,
            "Expected CSRF rejection without a token — enforcement is not "
            "active, so the positive assertion below would be meaningless.",
        )

        # 4. With the meta-tag token in X-CSRFToken (what patched main.js
        #    sends) the POST passes CSRF and the legacy submit succeeds.
        ok = client.post(
            reverse("registration:add_to_cart"),
            payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertNotEqual(
            ok.status_code,
            403,
            f"Meta-tag CSRF token was rejected ({ok.status_code}): "
            f"{ok.content!r}. The legacy-form fix is broken.",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
