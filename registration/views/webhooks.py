import json
import logging
import os
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from square.utils.webhooks_helper import verify_signature

from registration import payments
from registration.models import PaymentWebhookNotification
from registration.views import common

logger = logging.getLogger(__name__)


# Webhook age-window defaults. Past tolerance bounds capture-replay attacks
# where an attacker resurrects a months-old signed webhook after the
# event_id replay record has been purged. Future tolerance allows for
# clock skew between provider and us. Both env-overridable so ops can
# widen if a provider's retry semantics demand it.
#   WEBHOOK_MAX_AGE_SECONDS   (default 3600  / 1h)
#   WEBHOOK_FUTURE_SKEW_SECONDS (default  300 / 5m)
WEBHOOK_MAX_AGE_SECONDS = int(os.getenv("WEBHOOK_MAX_AGE_SECONDS", "3600"))
WEBHOOK_FUTURE_SKEW_SECONDS = int(os.getenv("WEBHOOK_FUTURE_SKEW_SECONDS", "300"))


def _parse_iso8601_utc(value):
    """Parse an ISO 8601 timestamp (with optional trailing Z) into a tz-aware
    UTC datetime. Returns None if the value can't be parsed.
    """
    if not value:
        return None
    try:
        # ``datetime.fromisoformat`` accepts "+00:00" but historically not
        # the trailing "Z". Normalize.
        normalized = value.rstrip("Z") + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def webhook_within_age_window(timestamp_iso, *, source):
    """Return True if *timestamp_iso* (ISO 8601 string) is within the
    configured webhook age window. Logs a WARNING and returns False on any
    parse error or out-of-window timestamp. *source* is a short label
    ('square', 'paypal') used in the log line for triage.

    OWASP A02 (Cryptographic Failures) / Webhook Security Cheat Sheet:
    bound the validity period of a signed payload so capture-replay
    attempts surface even when the event_id replay record has been
    purged or never observed.
    """
    ts = _parse_iso8601_utc(timestamp_iso)
    if ts is None:
        logger.warning(
            "webhook[%s]: missing or unparseable timestamp %r; rejecting",
            source,
            timestamp_iso,
        )
        return False
    now = datetime.now(timezone.utc)
    age = (now - ts).total_seconds()
    if age > WEBHOOK_MAX_AGE_SECONDS:
        logger.warning(
            "webhook[%s]: timestamp %s is %.0fs old (> %ds max); rejecting",
            source,
            ts.isoformat(),
            age,
            WEBHOOK_MAX_AGE_SECONDS,
        )
        return False
    if age < -WEBHOOK_FUTURE_SKEW_SECONDS:
        logger.warning(
            "webhook[%s]: timestamp %s is %.0fs in the future (> %ds skew); rejecting",
            source,
            ts.isoformat(),
            -age,
            WEBHOOK_FUTURE_SKEW_SECONDS,
        )
        return False
    return True


@require_POST
@csrf_exempt
def square_webhook(request):
    square_signature = request.headers.get("X-Square-HMACSHA256-Signature")
    notification_url = request.build_absolute_uri()

    signature_valid = verify_signature(
        request_body=request.body.decode("utf-8"),
        signature_header=square_signature,
        signature_key=settings.SQUARE_WEBHOOK_SIGNATURE_KEY,
        notification_url=notification_url,
    )

    if not signature_valid:
        logger.warning("Invalid signature in Square request")
        return common.abort(403, "Forbidden: invalid signature")

    try:
        request_body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return common.abort(400, "Unable to decode JSON")

    if "event_id" not in request_body:
        return common.abort(400, "Missing event_id")

    # Bound the webhook's age. Square embeds an ISO 8601 timestamp in
    # ``created_at`` at the top of the body. See webhook_within_age_window
    # for the rationale.
    if not webhook_within_age_window(request_body.get("created_at"), source="square"):
        return common.abort(400, "Webhook timestamp out of window")

    event_id = request_body["event_id"]
    event_type = request_body.get("type")

    # Check to see if webhook was already stored:
    existing = PaymentWebhookNotification.objects.filter(event_id=event_id)
    if existing.exists():
        return common.success(200)

    # Store the verified event notification
    notification = PaymentWebhookNotification(
        event_id=event_id,
        event_type=event_type,
        body=request_body,
        headers=dict(request.headers),
    )
    try:
        notification.save()
    except IntegrityError as e:
        logger.warning(f"Conflict: event_id {event_id} already exists")
        logger.debug(e)
        return common.success(200)

    process_webhook(notification)

    return common.success(200)


def process_webhook(notification: PaymentWebhookNotification):
    result = False
    if notification.body["type"] == "refund.updated":
        result = payments.process_webhook_refund_update(notification)
    elif notification.body["type"] == "refund.created":
        result = payments.process_webhook_refund_created(notification)
    elif notification.body["type"] == "payment.updated":
        result = payments.process_webhook_payment_updated(notification)
    elif notification.body["type"] in ("dispute.created", "dispute.state.updated"):
        result = payments.process_webhook_dispute_created_or_updated(notification)

    notification.processed = result
    notification.save()
