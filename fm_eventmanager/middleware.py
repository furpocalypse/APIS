"""Project-wide middleware. Ordering is pinned in settings.MIDDLEWARE:
RequireClientIPMiddleware is positioned strictly before AxesMiddleware so
axes never observes an unresolved client IP (plan D-IP / RT-B5)."""

from __future__ import annotations

import ipaddress
import logging

from django.conf import settings
from django.http import HttpResponseForbidden

# Plan D-IP: the ONE client-IP resolver. clientip is model-free (stdlib +
# allauth only), so this top-level import is safe while Django resolves
# MIDDLEWARE / AXES_CLIENT_IP_CALLABLE before app readiness — no
# AppRegistryNotReady, no lazy imports anywhere in this module.
from fm_eventmanager.clientip import get_client_ip

logger = logging.getLogger("fm_eventmanager.security")


def axes_client_ip(request):
    """IP-resolution callable for django-axes (``AXES_CLIENT_IP_CALLABLE``).

    Returns the single resolver's result verbatim — **no ``"0.0.0.0"``
    sentinel**. Coercing an unresolved IP to a constant would collapse
    every attacker into one axes lockout bucket (fail-open, the inverse of
    intent). RequireClientIPMiddleware runs before AxesMiddleware and
    rejects requests whose IP cannot be resolved, so axes never sees a
    ``None`` here in the enforced (production) path. ASVS V13.1.4.
    """
    return get_client_ip(request)


class RequireClientIPMiddleware:
    """Fail closed when the real client IP cannot be determined.

    nginx (the trust boundary, plan S13) overwrites the trusted client-IP
    header with the real client after the realip module rewrites
    ``$remote_addr`` from a verified upstream. allauth's resolver (the one
    re-exported by :mod:`fm_eventmanager.clientip`) reads that header. If
    it is missing — nginx misconfigured, deploy bypasses nginx, or a
    future change removes the boundary — every IP-derived control (axes
    lockout, allauth login records, audit logging, rate limits) silently
    no-ops. That is fail-open; this middleware makes it fail-closed.

    OWASP A01, ASVS V13.1.4. Behaviour:

      * ``DEBUG=True``: no-op (local runserver / tests / dev). Captured at
        construction so the prod hot path has no extra branch.
      * ``DEBUG=False``: resolve the client IP; if ``None``, return a
        generic 403 (the message intentionally leaks nothing about the
        trust chain) and log the reason at WARNING on the
        ``fm_eventmanager.security`` logger. Otherwise stash the resolved
        IP on ``request.client_ip`` for downstream reuse.

    The WARNING line is PII-safe and triage-sufficient: it carries the
    path and the (attacker-controlled, non-PII) spoof-header values only —
    never session id, cookie, Authorization, or request body (plan BT-B5).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # DEBUG is fixed for a process; capture once to avoid a per-request
        # settings dereference on the prod hot path.
        self._enforce = not settings.DEBUG
        # MED-13: parse TRUSTED_PROXY_CIDRS once. When non-empty (and
        # enforcing), the peer that connected to the app (REMOTE_ADDR)
        # MUST be inside the allowlist before any forwarded client-IP
        # header is honored — a request reaching the Service directly, or
        # via a misconfigured ingress, cannot inject a spoofed XFF. Empty
        # list = disabled (dev/test, or the nginx/T1 topology that does
        # its own realip-based origin lock). ASVS V13.1.4.
        self._trusted_proxy_networks = []
        for cidr in getattr(settings, "TRUSTED_PROXY_CIDRS", []):
            try:
                self._trusted_proxy_networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError as exc:
                # Log the ValueError (it names the rejected entry and *why*
                # it is invalid) rather than the raw settings value. This
                # is both more useful to operators and keeps the log
                # argument sourced from the stdlib exception, not directly
                # from the `django.conf.settings` read (a config CIDR is
                # not a secret, but CodeQL's clear-text-logging query
                # classifies any settings-attribute read as sensitive).
                logger.warning(
                    "RequireClientIPMiddleware: ignoring invalid TRUSTED_PROXY_CIDRS entry (%s)",
                    exc,
                )

    def _peer_is_trusted_proxy(self, request) -> bool:
        remote_addr = request.META.get("REMOTE_ADDR")
        if not remote_addr:
            return False
        try:
            peer = ipaddress.ip_address(remote_addr)
        except ValueError:
            return False
        return any(peer in net for net in self._trusted_proxy_networks)

    def __call__(self, request):
        if self._enforce:
            if self._trusted_proxy_networks and not self._peer_is_trusted_proxy(request):
                logger.warning(
                    "RequireClientIPMiddleware: rejecting request whose peer "
                    "is not in TRUSTED_PROXY_CIDRS. path=%s host=%s "
                    "remote_addr=%r xff=%r",
                    request.path,
                    request.get_host(),
                    request.META.get("REMOTE_ADDR"),
                    request.headers.get("X-Forwarded-For"),
                )
                return HttpResponseForbidden("Forbidden: untrusted request origin.")
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
                return HttpResponseForbidden("Forbidden: client identity could not be determined.")
            request.client_ip = ip

        return self.get_response(request)
