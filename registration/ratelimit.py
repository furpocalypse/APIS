"""MED-3 / MED-4 (OWASP API4 / ASVS V11.1.1 / DoS + Webhook cheat sheets).

A thin wrapper over ``django_ratelimit`` that returns a uniform JSON 429
instead of django-ratelimit's default ``PermissionDenied`` (403) — these
endpoints are JSON/AJAX/webhook surfaces and a 429 with ``Retry-After``
semantics is the correct contract (REST + DoS cheat sheets).

Rate-limit buckets key off the project's *trusted* client-IP resolver
(``fm_eventmanager.clientip.get_client_ip`` — the same allauth-pinned
resolver axes/allauth use) so buckets are correct behind Cloudflare /
App Gateway, not keyed off a spoofable ``REMOTE_ADDR``.

Idempotency keys already blunt *accidental* retries on the payment/submit
flows; this adds the *deliberate* flood / enumeration backstop. Signature
verification remains the real control on webhooks — this only caps volume
if a signing key ever leaks.
"""

import logging
from functools import wraps

from django.http import JsonResponse
from django_ratelimit.core import is_ratelimited

from fm_eventmanager.clientip import get_client_ip

logger = logging.getLogger("fm_eventmanager.security")


def _trusted_ip_key(group, request) -> str:
    """django-ratelimit key callable → the trusted client IP (or ``""``)."""
    return get_client_ip(request) or ""


def rate_limited_json(*, rate: str, methods=("POST",), group: str | None = None):
    """Decorate a JSON view with an IP-keyed fixed-window rate limit.

    ``rate`` is django-ratelimit syntax (e.g. ``"10/h"``, ``"100/m"``).
    On limit: log a security WARNING (no PII) and return HTTP 429
    ``{"success": false, "reason": "Rate limit exceeded"}``. Fails *open*
    if the cache backend is unavailable — abuse control must not take down
    a legitimate registration flow.
    """

    def decorator(view):
        view_group = group or f"{view.__module__}.{view.__qualname__}"

        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            try:
                limited = is_ratelimited(
                    request,
                    group=view_group,
                    key=_trusted_ip_key,
                    rate=rate,
                    method=methods,
                    increment=True,
                )
            except Exception:
                # Cache backend down / misconfigured: do not block traffic.
                logger.warning("rate-limit check failed open for group=%s", view_group)
                return view(request, *args, **kwargs)
            if limited:
                logger.warning("rate limit exceeded (group=%s, rate=%s)", view_group, rate)
                return JsonResponse(
                    {"success": False, "reason": "Rate limit exceeded"},
                    status=429,
                )
            return view(request, *args, **kwargs)

        return _wrapped

    return decorator
