import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from registration import paypal_webhook_handlers as pph
from registration.models import PaymentWebhookNotification
from registration.ratelimit import rate_limited_json
from registration.views import common
from registration.views.webhook_age import (
    webhook_signature_bypassed,
    webhook_within_age_window,
)

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None

logger = logging.getLogger(__name__)


def _sentry_capture(message: str, level: str = "warning", **tags) -> None:
    if sentry_sdk is None:
        return
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("integration", "paypal_webhook")
        for key, value in tags.items():
            scope.set_tag(key, value)
        sentry_sdk.capture_message(message, level=level)


PAYPAL_LIVE_API_BASE = "https://api-m.paypal.com"
PAYPAL_SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"
PAYPAL_HTTP_TIMEOUT = 10

# MED-7: cap the cached-token lifetime well below PayPal's stated ~1h so a
# refresh always happens before expiry; never trust expires_in to exceed
# this. Keyed per API base so a sandbox token is never reused live.
PAYPAL_TOKEN_CACHE_MAX_TTL = 3000  # 50 min
PAYPAL_TOKEN_CACHE_SAFETY = 600  # refresh 10 min before PayPal's expiry


def _paypal_token_cache_key() -> str:
    return f"paypal:access_token:{_paypal_api_base()}"


def _paypal_api_base() -> str:
    env = getattr(settings, "PAYPAL_ENVIRONMENT", "") or ""
    if env.lower().startswith("p"):
        return PAYPAL_LIVE_API_BASE
    return PAYPAL_SANDBOX_API_BASE


def invalidate_paypal_access_token() -> None:
    """MED-7: drop the cached token (called on a 401 from a downstream call)."""
    try:
        cache.delete(_paypal_token_cache_key())
    except Exception:
        # A cache outage must not break the fail-closed verify path.
        logger.warning("Could not invalidate cached PayPal token")


def _get_paypal_access_token(*, force_refresh: bool = False) -> str | None:
    """Return a PayPal OAuth2 bearer token, cached in Redis (MED-7).

    Tokens are valid ~1h; we cache them for at most
    ``PAYPAL_TOKEN_CACHE_MAX_TTL`` (and 10 min below ``expires_in`` if
    PayPal returns a shorter one) so webhook latency is decoupled from
    PayPal's OAuth uptime. ``force_refresh`` bypasses the cache (used after
    a 401). Returns the token on success, None on any failure.
    """
    cache_key = _paypal_token_cache_key()
    if not force_refresh:
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None  # cache down → fall through to a live fetch
        if cached:
            return cached

    client_id = getattr(settings, "PAYPAL_CLIENT_ID", "") or ""
    client_secret = getattr(settings, "PAYPAL_CLIENT_SECRET", "") or ""
    if not client_id or not client_secret:
        logger.warning("PayPal client credentials not configured")
        return None

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    url = f"{_paypal_api_base()}/v1/oauth2/token"
    data = b"grant_type=client_credentials"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=PAYPAL_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        logger.error("Failed to fetch PayPal OAuth2 token: %s", e)
        return None

    token = payload.get("access_token")
    if not token:
        return None

    try:
        expires_in = int(payload.get("expires_in", 0))
    except (TypeError, ValueError):
        expires_in = 0
    ttl = PAYPAL_TOKEN_CACHE_MAX_TTL
    if expires_in:
        ttl = max(60, min(expires_in - PAYPAL_TOKEN_CACHE_SAFETY, ttl))
    try:
        cache.set(cache_key, token, timeout=ttl)
    except Exception:
        logger.warning("Could not cache PayPal token (continuing without cache)")
    return token


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
    if webhook_signature_bypassed(request):
        return True

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
        logger.error("PAYPAL_WEBHOOK_ID is not configured")
        _sentry_capture(
            "PAYPAL_WEBHOOK_ID is not configured",
            level="error",
            reason="missing_webhook_id",
        )
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
    body = json.dumps(verify_body).encode("utf-8")

    def _do_verify(bearer: str):
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {bearer}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=PAYPAL_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning("PayPal verify-webhook-signature returned HTTP %s", resp.status)
                return None
            return json.loads(resp.read().decode("utf-8"))

    # MED-7 / LOW-3: a 401 means the (possibly cached) token is stale.
    # Invalidate, force-refresh, and retry exactly once. This bounded
    # retry-on-auth-failure is the proportionate circuit for a fail-closed
    # webhook verify — a full circuit-breaker is deliberately out of scope.
    # Any non-401 HTTP error, transport failure, or parse error fails
    # closed (returns False) as before.
    payload = None
    for attempt in (1, 2):
        try:
            payload = _do_verify(token)
            break
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                logger.warning("PayPal verify got 401; refreshing token and retrying once")
                invalidate_paypal_access_token()
                token = _get_paypal_access_token(force_refresh=True)
                if not token:
                    return False
                continue
            logger.error("PayPal verify-webhook-signature HTTP error: %s", e.code)
            return False
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            logger.error("PayPal verify-webhook-signature call failed: %s", e)
            return False

    if not payload or payload.get("verification_status") != "SUCCESS":
        return False

    # Plan S2: the freshness check lives INSIDE the signature-verified
    # path. paypal-transmission-time is part of PayPal's signature input,
    # so its integrity is only established once the signature verifies —
    # enforce the age window here, not as a separate pre-check. A
    # @patch-mocked verify_signature (unit tests) or the E2E bypass
    # (returned True above) therefore bypasses freshness together with the
    # signature, which is correct: an unverified timestamp is meaningless.
    return webhook_within_age_window(
        request.headers.get("paypal-transmission-time"), source="paypal"
    )


# MED-4: signature verification is the real control; cap volume so a
# leaked signing key can't flood forged-but-valid events. 100/min/source.
@rate_limited_json(rate="100/m")
@require_POST
@csrf_exempt
def paypal_webhook(request):
    if not verify_signature(request):
        logger.warning("Invalid signature in PayPal webhook request")
        _sentry_capture(
            "PayPal webhook rejected with invalid signature",
            level="warning",
            reason="invalid_signature",
        )
        return common.abort(403, "Forbidden: invalid signature")

    try:
        request_body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return common.abort(400, "Unable to decode JSON")

    if "id" not in request_body:
        return common.abort(400, "Missing id")

    # Freshness on paypal-transmission-time is enforced INSIDE
    # verify_signature (plan S2 — it is meaningful only once the signature
    # is verified). The durable PaymentWebhookNotification (integration,
    # event_id) store (plan S38) is the replay defence for late but valid
    # first deliveries.

    event_id = request_body["id"]
    event_type = request_body.get("event_type")

    # Check to see if webhook was already stored:
    existing = PaymentWebhookNotification.objects.filter(event_id=event_id)
    if existing.exists():
        return common.success(200)

    # Store the verified event notification
    notification = PaymentWebhookNotification(
        integration="paypal",
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


_HANDLERS = {
    "PAYMENT.CAPTURE.COMPLETED": pph.handle_capture_completed,
    "PAYMENT.CAPTURE.DENIED": pph.handle_capture_denied,
    "PAYMENT.CAPTURE.REFUNDED": pph.handle_capture_refunded,
    "PAYMENT.CAPTURE.REVERSED": pph.handle_capture_reversed,
    "PAYMENT.SALE.REFUNDED": pph.handle_sale_refunded_v1,
    "CUSTOMER.DISPUTE.CREATED": pph.handle_dispute_created,
    "CUSTOMER.DISPUTE.UPDATED": pph.handle_dispute_updated,
    "CUSTOMER.DISPUTE.RESOLVED": pph.handle_dispute_resolved,
}


def process_webhook(notification: PaymentWebhookNotification):
    event_type = notification.body.get("event_type")
    handler = _HANDLERS.get(event_type)
    if handler is None:
        logger.info("PayPal webhook: no handler for event_type %r", event_type)
        result = False
    else:
        try:
            result = handler(notification)
        except Exception:
            logger.exception(
                "PayPal webhook handler for %s crashed; marking unprocessed",
                event_type,
            )
            result = False

    notification.processed = result
    notification.save(update_fields=["processed"])
