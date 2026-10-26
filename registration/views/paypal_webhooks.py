import base64
import hmac
import json
import logging
import urllib.error
import urllib.request
import zlib
from typing import Any, Literal

import requests
from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from registration import paypal_webhook_handlers as pph
from registration.models import Order
from registration.ratelimit import rate_limited_json
from registration.views import common

logger = logging.getLogger(__name__)


PAYPAL_LIVE_API_BASE = "https://api-m.paypal.com"
PAYPAL_SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"
PAYPAL_HTTP_TIMEOUT = 10

# MED-7: cap the cached-token lifetime well below PayPal's stated ~1h so a
# refresh always happens before expiry; never trust expires_in to exceed
# this. Keyed per API base so a sandbox token is never reused live.
PAYPAL_TOKEN_CACHE_MAX_TTL = 3000  # 50 min
PAYPAL_TOKEN_CACHE_SAFETY = 600  # refresh 10 min before PayPal's expiry


PAYPAL_CERT = "paypal_webhook_public_key.pem"


def validate_paypal_webhook(request: HttpRequest) -> Literal[True]:
    """
    https://developer.paypal.com/api/rest/webhooks/rest/#self-verification-method
    """
    cert: bytes
    try:
        with open(PAYPAL_CERT, "rb") as cert_file:
            cert = cert_file.read()
    except OSError as ose:
        url_string: str = request.headers["paypal-cert-url"]
        url = urllib.parse(url_string)
        if url.protocol != "https":
            raise Exception(f"PayPal cert URL is not https but {url.protocol!r}.") from ose
        if url.server != "api.paypal.com":
            raise Exception(f"PayPal cert URL isn't to paypal but {url.server!r}.") from ose
        response = requests.get(url_string)
        if not response.ok:
            raise Exception("PayPal cert url bad.") from ose
        cert = response.content
        if cert is None or len(cert) < 64:
            raise Exception("PayPal cert is missing or too small.") from ose
        with open(PAYPAL_CERT, "wb") as cert_file:
            cert_file.write(cert)

    if not hmac.compare_digest(
        hmac.digest(
            key=cert,
            msg=f"{request.headers['paypal-transmission-id']}|{request.headers['paypal-transmission-time']}|{settings.PAYPAL_WEBHOOK_ID}|{zlib.crc32(request.body)}".encode(),
            digest="sha256",
        ),
        base64.b64decode(request.headers["paypal-transmission-sig"]),
    ):
        raise Exception("Webhook validation failed.")

    return True


def paypal_completed_event(event: dict[str, Any], rec: Order) -> HttpResponse:
    rec.status = Order.COMPLETED
    rec.save()
    return common.success()


def paypal_refunded_event(event: dict[str, Any], rec: Order) -> HttpResponse:
    rec.status = Order.REFUNDED
    rec.save()
    return common.success(200)


def paypal_reversed_event(event: dict[str, Any], rec: Order) -> HttpResponse:
    rec.status = Order.REFUNDED
    rec.save()
    return common.success(200)


# MED-4: signature verification is the real control; cap volume so a
# leaked signing key can't flood forged-but-valid events. 100/min/source.
@rate_limited_json(rate="100/m")
@require_POST
@csrf_exempt
def process_webhook(request: HttpRequest) -> HttpResponse:
    validate_paypal_webhook(request)

    event = json.loads(request.body)
    try:
        rec: Order | None = Order.objects.filter(id=event["resource"]["invoice_id"]).first()
    except Exception as exp:
        raise Exception("Event do malform") from exp
    if rec is None:
        raise Exception("We shouldn't be getting a webhook for a transaction we don't know of.")

    ignore_events = {
        "PAYMENT.AUTHORIZATION.CREATED",
        "PAYMENT.AUTHORIZATION.VOIDED",
        "PAYMENT.CAPTURE.DECLINED",
        "PAYMENT.CAPTURE.PENDING",
        "PAYMENT.REFUND.PENDING",
        "PAYMENT.REFUND.FAILED",
        "PAYMENT.CAPTURE.DENIED",
    }

    listen_events = {
        "PAYMENT.CAPTURE.COMPLETED": pph.handle_capture_completed,
        "PAYMENT.CAPTURE.DENIED": pph.handle_capture_denied,
        "PAYMENT.CAPTURE.REFUNDED": pph.handle_capture_refunded,
        "PAYMENT.CAPTURE.REVERSED": pph.handle_capture_reversed,
        "PAYMENT.SALE.REFUNDED": pph.handle_sale_refunded_v1,
        "CUSTOMER.DISPUTE.CREATED": pph.handle_dispute_created,
        "CUSTOMER.DISPUTE.UPDATED": pph.handle_dispute_updated,
        "CUSTOMER.DISPUTE.RESOLVED": pph.handle_dispute_resolved,
    }

    if event["event_type"] in ignore_events:
        return common.success(200)
    if event["event_type"] not in listen_events:
        return HttpResponseBadRequest()

    return listen_events[event["event_type"]](event, rec)


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
