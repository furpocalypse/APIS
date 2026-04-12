import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from registration import payments
from registration.models import PaymentWebhookNotification
from registration.views import common

logger = logging.getLogger(__name__)


PAYPAL_LIVE_API_BASE = "https://api-m.paypal.com"
PAYPAL_SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"
PAYPAL_HTTP_TIMEOUT = 10


def _paypal_api_base() -> str:
    env = getattr(settings, "PAYPAL_ENVIRONMENT", "") or ""
    if env.lower().startswith("p"):
        return PAYPAL_LIVE_API_BASE
    return PAYPAL_SANDBOX_API_BASE


def _get_paypal_access_token() -> str | None:
    """Fetch an OAuth2 bearer token from PayPal using client credentials.

    Returns the token on success, None on any failure.
    """
    client_id = getattr(settings, "PAYPAL_CLIENT_ID", "") or ""
    client_secret = getattr(settings, "PAYPAL_CLIENT_SECRET", "") or ""
    if not client_id or not client_secret:
        logger.warning("PayPal client credentials not configured")
        return None

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode(
        "ascii"
    )
    url = f"{_paypal_api_base()}/v1/oauth2/token"
    data = b"grant_type=client_credentials"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=PAYPAL_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("access_token")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        logger.error("Failed to fetch PayPal OAuth2 token: %s", e)
        return None


def verify_signature(request) -> bool:
    """Verify a PayPal webhook signature by calling PayPal's verification API.

    Uses POST /v1/notifications/verify-webhook-signature. See:
    https://developer.paypal.com/api/rest/webhooks/rest/#link-verifywebhookevent

    We use ``urllib.request`` from the stdlib because ``requests`` is not a
    declared dependency in pyproject.toml and the paypalserversdk client does
    not expose a webhook-verification controller.

    Returns True only if PayPal responds 200 with verification_status=SUCCESS.
    Any missing config, parse error, or transport failure returns False
    (fail-closed).
    """
    required_headers = (
        "paypal-auth-algo",
        "paypal-cert-url",
        "paypal-transmission-id",
        "paypal-transmission-sig",
        "paypal-transmission-time",
    )
    header_values = {h: request.headers.get(h) for h in required_headers}
    missing = [h for h, v in header_values.items() if not v]
    if missing:
        logger.warning("PayPal webhook missing required headers: %s", missing)
        return False

    webhook_id = getattr(settings, "PAYPAL_WEBHOOK_ID", "") or ""
    if not webhook_id:
        logger.warning("PAYPAL_WEBHOOK_ID is not configured")
        return False

    try:
        webhook_event = json.loads(request.body)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("PayPal webhook body is not valid JSON: %s", e)
        return False

    token = _get_paypal_access_token()
    if not token:
        return False

    verify_body = {
        "auth_algo": header_values["paypal-auth-algo"],
        "cert_url": header_values["paypal-cert-url"],
        "transmission_id": header_values["paypal-transmission-id"],
        "transmission_sig": header_values["paypal-transmission-sig"],
        "transmission_time": header_values["paypal-transmission-time"],
        "webhook_id": webhook_id,
        "webhook_event": webhook_event,
    }

    url = f"{_paypal_api_base()}/v1/notifications/verify-webhook-signature"
    req = urllib.request.Request(
        url,
        data=json.dumps(verify_body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=PAYPAL_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(
                    "PayPal verify-webhook-signature returned HTTP %s", resp.status
                )
                return False
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        logger.error("PayPal verify-webhook-signature call failed: %s", e)
        return False

    return payload.get("verification_status") == "SUCCESS"


@require_POST
@csrf_exempt
def paypal_webhook(request):
    if not verify_signature(request):
        logger.warning("Invalid signature in PayPal webhook request")
        return common.abort(403, "Forbidden: invalid signature")

    try:
        request_body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return common.abort(400, "Unable to decode JSON")

    if "id" not in request_body:
        return common.abort(400, "Missing id")

    event_id = request_body["id"]
    event_type = request_body.get("event_type")

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
    # TODO: This dispatcher still routes to Square handlers using Square event
    # names. A PayPal-specific dispatcher rewrite is tracked as a follow-up
    # (see issue #6 / dispatcher-rewrite plan). For now we only update the
    # top-level field read so idempotency + storage paths use PayPal's
    # ``event_type`` key rather than Square's ``type``.
    event_type = notification.body.get("event_type")
    result = False
    if event_type == "refund.updated":
        result = payments.process_webhook_refund_update(notification)
    elif event_type == "refund.created":
        result = payments.process_webhook_refund_created(notification)
    elif event_type == "payment.updated":
        result = payments.process_webhook_payment_updated(notification)
    elif event_type in ("dispute.created", "dispute.state.updated"):
        result = payments.process_webhook_dispute_created_or_updated(notification)

    notification.processed = result
    notification.save()
