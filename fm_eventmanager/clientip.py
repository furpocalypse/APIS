"""Single client-IP trust-boundary resolver (plan Decision D-IP).

There is exactly ONE client-IP resolver in the codebase and it lives here.
It is a deliberate, version-pinned re-export of allauth's resolver:

    allauth.core.internal.httpkit.get_client_ip

Why re-export the one allauth symbol rather than (a) re-implement it or
(b) keep the old leftmost-XFF reader:

* allauth itself keys login records / rate limits off *its* resolver. axes
  (via AXES_CLIENT_IP_CALLABLE), audit logging, and the fail-closed
  RequireClientIPMiddleware must key off the SAME value or the controls
  disagree. Re-using allauth's function guarantees byte-identical results.
* A hand-rolled copy silently diverges on the next allauth change
  (port/bracket stripping, the trusted-header-vs-XFF-vs-REMOTE_ADDR branch
  in httpkit:get_client_ip). The pinned allauth version + the parity
  contract test (registration/tests) lock this dependency.

Documented behaviour of the underlying allauth resolver (verified against
the installed source): when ``ALLAUTH_TRUSTED_CLIENT_IP_HEADER`` is
non-empty it returns *only* that header (``None`` if absent); when empty
it falls back through XFF then ``REMOTE_ADDR``. settings_base enforces a
non-empty header under ``APIS_ENV=production`` so the trust property holds
in prod; tests/dev intentionally run with it empty (REMOTE_ADDR fallback).

This module imports ONLY stdlib + allauth (no ``registration.*``, no
Django app/model import) so it is safe to import at module top from
``fm_eventmanager.middleware`` while Django resolves MIDDLEWARE /
AXES_CLIENT_IP_CALLABLE, before the app registry is ready. An
import-purity test asserts this closure stays model-free.
"""

from __future__ import annotations

from allauth.core.internal.httpkit import get_client_ip as _allauth_get_client_ip

__all__ = ["get_client_ip"]


def get_client_ip(request):
    """The one resolver: allauth's, with its documented contract enforced.

    allauth's resolver does ``request.META["REMOTE_ADDR"]`` (subscript) in
    its empty-trusted-header fallback branch, which raises ``KeyError`` on
    requests that lack ``REMOTE_ADDR`` (axes login/logout signal requests,
    synthetic/test requests). The resolver's contract — and everything
    keying off it (the fail-closed middleware, axes, audit) — expects
    ``None`` when the IP is unresolvable, not a crash. We do NOT
    re-implement allauth's algorithm (single source of truth, parity
    contract test); we only convert the unresolvable case to the
    contracted ``None``. Import-pure (stdlib + allauth only)."""
    try:
        return _allauth_get_client_ip(request)
    except KeyError:
        return None
