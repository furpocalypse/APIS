"""Project-wide middleware. See fm_eventmanager/settings.py.* for ordering."""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpResponseForbidden

logger = logging.getLogger("fm_eventmanager.security")


def axes_client_ip(request):
    """IP-resolution callable for django-axes.

    Routes through our central :func:`registration.views.common.get_client_ip`
    so axes lockout state keys off the same client IP that allauth sees
    (the trusted edge's ``X-Real-IP`` header). Without this, axes would
    fall back to its own IP detection against the spoofable
    ``REMOTE_ADDR`` and produce per-attacker lockout state that a real
    user would also share. ASVS V13.1.4.

    The function is imported via dotted-path string in the
    ``AXES_CLIENT_IP_CALLABLE`` setting; importing
    ``registration.views.common`` lazily avoids an import cycle at app
    startup (this middleware module is loaded before app readiness).
    """
    from registration.views.common import get_client_ip

    return get_client_ip(request) or "0.0.0.0"


class RequireClientIPMiddleware:
    """Fail closed when the real client IP cannot be determined.

    The proxy/header pipeline (see CLAUDE.md and nginx.conf) makes nginx the
    single trust boundary that writes ``X-Real-IP`` to the real client.
    allauth's ``get_client_ip`` reads that header. *If the header is missing*
    — because nginx is misconfigured, the deploy bypasses nginx, or a future
    change removes the trust boundary — every IP-derived security control
    (rate limiting, allauth login records, audit logging, abuse signals)
    silently no-ops. That is fail-open, the inverse of what we want.

    OWASP A01 (Broken Access Control), ASVS V13.1.4, OWASP "HTTP Headers"
    cheat sheet: derive the client IP only from a header set by the trusted
    edge, AND fail closed if that derivation fails.

    Behaviour:
      * ``DEBUG=True``: no-op. Local runserver / tests / dev flows are
        unaffected. The flag is captured at construction time so the
        per-request hot path in production has no extra branch.
      * ``DEBUG=False``: call ``registration.views.common.get_client_ip``;
        if it returns ``None``, return a generic 403. Otherwise stash the
        resolved IP on ``request.client_ip`` for downstream code that
        wants to skip re-resolution.

    The 403 response intentionally does NOT explain the precise cause to
    the caller (an attacker fingerprinting the trust chain learns nothing
    from a generic message). The reason is logged at WARNING.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Capture DEBUG at construction time. Flipping DEBUG at runtime is
        # not supported by Django anyway, and this lets us avoid a per-
        # request settings dereference in the prod hot path.
        self._enforce = not settings.DEBUG

    def __call__(self, request):
        if self._enforce:
            # Imported lazily so the middleware module can be imported
            # before app loading (registration.views imports app models).
            from registration.views.common import get_client_ip

            ip = get_client_ip(request)
            if ip is None:
                logger.warning(
                    "RequireClientIPMiddleware: rejecting request with "
                    "unresolvable client IP. path=%s host=%s xff=%r xri=%r",
                    request.path,
                    request.get_host(),
                    request.headers.get("X-Forwarded-For"),
                    request.headers.get("X-Real-IP"),
                )
                return HttpResponseForbidden(
                    "Forbidden: client identity could not be determined."
                )
            # Convenience cache. Downstream code (rate-limit decorators,
            # views that log the IP for audit) can read request.client_ip
            # without re-doing the header parse.
            request.client_ip = ip

        return self.get_response(request)
