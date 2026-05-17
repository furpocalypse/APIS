"""Webhook freshness + signature-bypass primitives (plan S2/S18/S19).

Neutral, non-view module so both ``webhooks`` (Square) and
``paypal_webhooks`` import it as a package sibling (mirrors the existing
``registration/views/common.py`` shared-helper precedent) — no cross-view
import edge, no cycle. Imports only stdlib + Django (``settings``,
``dateparse``, ``timezone``); reads its tunables from settings **at call
time** so ``@override_settings`` works (plan S19/S20).

Design (plan S2):

* The age window is a *freshness* check that is only meaningful on a
  cryptographically signature-verified request — the timestamp's integrity
  is guaranteed by the signature, not on its own. Callers therefore
  enforce ``webhook_within_age_window`` ONLY when the signature was really
  verified; when signature verification is bypassed
  (``webhook_signature_bypassed`` — E2E mock path) the window is bypassed
  too. Enforcing it without a verified signature is security theatre and a
  correctness bug (it rejected legitimate E2E / handler-stub traffic).
* Replay defence proper is the durable ``PaymentWebhookNotification``
  ``(integration, event_id)`` store (plan S38), so the window is sized to
  cover provider retry horizons (Square ≈ 72h, PayPal ≈ 3 days) and must
  never silently drop a legitimate *first* delivery — see
  ``WEBHOOK_MAX_AGE_SECONDS`` default in settings_base.
"""

from __future__ import annotations

import logging
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger("registration.views")


def webhook_signature_bypassed(request) -> bool:
    """The single shared predicate for "signature verification is bypassed".

    True only on the E2E mock path: ``settings.E2E_MODE`` (which
    settings_base forces False unless DEBUG, and hard-fails under
    ``APIS_ENV=production`` — plan S2b/RT-B2) AND the explicit
    ``X-E2E-Mock-Signature: 1`` header. Used by both providers'
    signature checks and by the age-window guards so the two can never
    disagree.
    """
    return bool(getattr(settings, "E2E_MODE", False)) and (
        request.headers.get("X-E2E-Mock-Signature") == "1"
    )


def _parse_utc(value):
    """Parse an ISO-8601 / RFC-3339 timestamp to an aware UTC datetime, or
    ``None``. Uses Django's ``parse_datetime`` (handles the trailing ``Z``
    and offsets on Py>=3.11; returns ``None`` on garbage) + Django's
    timezone helpers — no bespoke ``rstrip('Z')`` (that stripped *all*
    trailing Z and is redundant). Project uses ``USE_TZ``."""
    if not value:
        return None
    try:
        dt = parse_datetime(value)
    except (ValueError, TypeError):
        return None
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def webhook_within_age_window(timestamp_iso, *, source) -> bool:
    """True if *timestamp_iso* is within the configured age window.

    Reads ``settings.WEBHOOK_MAX_AGE_SECONDS`` /
    ``settings.WEBHOOK_FUTURE_SKEW_SECONDS`` at call time. Logs a WARNING
    and returns False on parse error / out-of-window. *source* labels the
    log line ('square' / 'paypal') for triage. Callers MUST skip this when
    ``webhook_signature_bypassed`` is true (see module docstring)."""
    max_age = int(getattr(settings, "WEBHOOK_MAX_AGE_SECONDS", 259200))
    future_skew = int(getattr(settings, "WEBHOOK_FUTURE_SKEW_SECONDS", 300))
    ts = _parse_utc(timestamp_iso)
    if ts is None:
        logger.warning(
            "webhook[%s]: missing or unparseable timestamp %r; rejecting",
            source,
            timestamp_iso,
        )
        return False
    age = (timezone.now() - ts).total_seconds()
    if age > max_age:
        logger.warning(
            "webhook[%s]: timestamp %s is %.0fs old (> %ds max); rejecting",
            source,
            ts.isoformat(),
            age,
            max_age,
        )
        return False
    if age < -future_skew:
        logger.warning(
            "webhook[%s]: timestamp %s is %.0fs in the future (> %ds skew); "
            "rejecting",
            source,
            ts.isoformat(),
            -age,
            future_skew,
        )
        return False
    return True
