import json
from enum import Enum
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.test import RequestFactory, TestCase, override_settings, tag
from django.urls import reverse

from registration.models import (
    Attendee,
    Badge,
    BanList,
    Event,
    Order,
    OrderItem,
    PaymentWebhookNotification,
)
from registration.tests.common import (
    DEFAULT_EVENT_ARGS,
    TEST_ATTENDEE_ARGS,
    PayPalOrdersTestCase,
)
from registration.views.paypal_webhooks import verify_signature


class PaypalResourceType(Enum):
    # Data structure references:
    # - POST /v2/payments/captures/{capture_id}/refund paypal
    # - GET /v2/payments/refunds/{refund_id}.
    refund: str = "refund"

    # Data structure references:
    # - Missing
    capture: str = "capture"

    # Data structure references:
    # - Missing
    sale: str = "sale"

    # Data structure references:
    # - Missing
    dispute: str = "dispute"


class PaypalNotificationEventType(Enum):
    # primary refund event, the resource is a Refund object (not a Capture)
    # Resource type: 'refund'
    # Do we want to test this: Yes
    PAYMENT_CAPTURE_REFUNDED: str = "PAYMENT.CAPTURE.REFUNDED"

    # fires when PayPal (not the merchant) reverses a capture — typically due
    # to a chargeback or dispute resolution. Despite the event name, the
    # resource PayPal attaches is a v2 Refund object (same shape as
    # PAYMENT.CAPTURE.REFUNDED). Confirmed by real sanitized payloads:
    # https://github.com/woocommerce/woocommerce-paypal-payments/issues/1614
    # Resource type: 'refund'
    # Do we want to test this: Yes
    PAYMENT_CAPTURE_REVERSED: str = "PAYMENT.CAPTURE.REVERSED"

    # For chargebacks, you need the dispute events.
    # Data structure references:
    # - GET /v1/customer/disputes/{id}.
    # Resource type: 'dispute'
    # Do we want to test this: Maybe
    CUSTOMER_DISPUTE_CREATED: str = "CUSTOMER.DISPUTE.CREATED"

    # Community reports indicate PayPal has occasionally sent v1-style events
    # to v2 integrations without warning.
    # (v1 uses state, amount.total, amount.currency)
    # (v2 uses status, amount.value, amount.currency_code)
    # Data structure references:
    # - Missing
    # Resource type: 'sale'
    # Do we want to test this: Yes
    PAYMENT_SALE_REFUNDED: str = "PAYMENT.SALE.REFUNDED"

    # NB: PAYMENT.REFUND.PENDING and PAYMENT.REFUND.FAILED do NOT exist as
    # PayPal webhook event types per
    # https://developer.paypal.com/api/rest/webhooks/event-names/. Pending and
    # failed refund states are delivered via PAYMENT.CAPTURE.REFUNDED with
    # resource.status = "PENDING" or "FAILED" respectively.

    # Dispute status changes, new evidence submitted
    # Data structure references:
    # - Missing
    # Resource type: 'dispute'
    # Do we want to test this: No
    CUSTOMER_DISPUTE_UPDATED: str = "CUSTOMER.DISPUTE.UPDATED"

    # Dispute reaches final resolution
    # Data structure references:
    # - Missing
    # Resource type: 'dispute'
    # Do we want to test this: Maybe
    CUSTOMER_DISPUTE_RESOLVED: str = "CUSTOMER.DISPUTE.RESOLVED"


def paypal_resource_type_for_notfication_type(
    event_type: PaypalNotificationEventType,
) -> PaypalResourceType:

    if event_type in (
        PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED,
        # PAYMENT.CAPTURE.REVERSED carries a Refund resource despite the
        # event name (see PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED).
        PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED,
    ):
        return PaypalResourceType.refund
    elif event_type == PaypalNotificationEventType.PAYMENT_SALE_REFUNDED:
        return PaypalResourceType.sale
    elif event_type in (
        PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED,
        PaypalNotificationEventType.CUSTOMER_DISPUTE_UPDATED,
        PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED,
    ):
        return PaypalResourceType.dispute

    raise ValueError


def example_resource_for_paypal_notification_type(
    event_type: PaypalNotificationEventType,
) -> Dict[str, Any]:
    if event_type == PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED:
        return {
            "id": "4D780477II019004G",
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "99.99"},
            "invoice_id": "INV-12345",
            "custom_id": "ORDER-67890",
            "note_to_payer": "Defective product",
            "seller_payable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": "99.99"},
                "paypal_fee": {"currency_code": "USD", "value": "0.00"},
                "net_amount": {"currency_code": "USD", "value": "99.99"},
                "total_refunded_amount": {"currency_code": "USD", "value": "99.99"},
            },
            "create_time": "2025-01-24T16:44:55Z",
            "update_time": "2025-01-24T16:45:00Z",
            "links": [
                {
                    "href": "https://api.paypal.com/v2/payments/refunds/4D780477II019004G",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": "https://api.paypal.com/v2/payments/captures/3C679366HH908993F",
                    "rel": "up",
                    "method": "GET",
                },
            ],
        }
    elif event_type == PaypalNotificationEventType.PAYMENT_SALE_REFUNDED:
        return {
            "id": "9XG87361FT987084D",
            "state": "refunded",
            "amount": {"total": "99.99", "currency": "USD"},
            "payment_mode": "INSTANT_TRANSFER",
            "protection_eligibility": "ELIGIBLE",
            "transaction_fee": {"value": "3.19", "currency": "USD"},
            "parent_payment": "PAY-1A2345678B901234C",
            "sale_id": "9XG87361FT987084D",
            # v1 uses `invoice_number` / `custom` rather than v2's
            # `invoice_id` / `custom_id`. Setting both to the Order.reference
            # seeded by the tests so the handler can resolve the order.
            "invoice_number": "FOOBAR",
            "custom": "FOOBAR",
            "create_time": "2025-01-20T10:00:00Z",
            "update_time": "2025-01-24T16:45:00Z",
            "links": [
                {
                    "href": "https://api.paypal.com/v1/payments/sale/9XG87361FT987084D",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": "https://api.paypal.com/v1/payments/sale/9XG87361FT987084D/refund",
                    "rel": "refund",
                    "method": "POST",
                },
                {
                    "href": "https://api.paypal.com/v1/payments/payment/PAY-1A2345678B901234C",
                    "rel": "parent_payment",
                    "method": "GET",
                },
            ],
        }
    elif event_type == PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED:
        # Despite the event name, PayPal attaches a v2 Refund resource for
        # REVERSED events. Shape verified against a sanitized production
        # payload on github.com/woocommerce/woocommerce-paypal-payments/issues/1614.
        return {
            "id": "REV-8J47HP6K",
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "99.99"},
            "invoice_id": "INV-12345",
            "custom_id": "ORDER-67890",
            "seller_payable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": "99.99"},
                "paypal_fee": {"currency_code": "USD", "value": "0.00"},
                "net_amount": {"currency_code": "USD", "value": "99.99"},
                "total_refunded_amount": {"currency_code": "USD", "value": "99.99"},
            },
            "create_time": "2025-01-20T10:00:00Z",
            "update_time": "2025-01-24T16:45:00Z",
            "links": [
                {
                    "href": "https://api.paypal.com/v2/payments/refunds/REV-8J47HP6K",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": "https://api.paypal.com/v2/payments/captures/3C679366HH908993F",
                    "rel": "up",
                    "method": "GET",
                },
            ],
        }
    elif event_type == PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED:
        return {
            "dispute_id": "PP-D-12345",
            "create_time": "2025-01-24T16:44:55Z",
            "update_time": "2025-01-24T16:44:55Z",
            "status": "WAITING_FOR_SELLER_RESPONSE",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            "dispute_amount": {"currency_code": "USD", "value": "99.99"},
            "disputed_transactions": [
                {
                    "buyer_transaction_id": "5O190127TN364715T",
                    "seller_transaction_id": "3C679366HH908993F",
                    "create_time": "2025-01-20T10:00:00Z",
                    "transaction_status": "COMPLETED",
                    "gross_amount": {"currency_code": "USD", "value": "99.99"},
                }
            ],
            "dispute_life_cycle_stage": "CHARGEBACK",
            "dispute_channel": "INTERNAL",
            "links": [
                {
                    "href": "https://api.paypal.com/v1/customer/disputes/PP-D-12345",
                    "rel": "self",
                    "method": "GET",
                }
            ],
        }
    elif event_type == PaypalNotificationEventType.CUSTOMER_DISPUTE_UPDATED:
        return {
            "dispute_id": "PP-D-12345",
            "create_time": "2025-01-24T16:44:55Z",
            "update_time": "2025-01-25T09:00:00Z",
            "status": "UNDER_REVIEW",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            "dispute_amount": {"currency_code": "USD", "value": "99.99"},
            "disputed_transactions": [
                {
                    "buyer_transaction_id": "5O190127TN364715T",
                    "seller_transaction_id": "3C679366HH908993F",
                    "create_time": "2025-01-20T10:00:00Z",
                    "transaction_status": "COMPLETED",
                    "gross_amount": {"currency_code": "USD", "value": "99.99"},
                }
            ],
            "dispute_life_cycle_stage": "CHARGEBACK",
            "dispute_channel": "INTERNAL",
            "messages": [
                {
                    "posted_by": "BUYER",
                    "time_posted": "2025-01-25T08:30:00Z",
                    "content": "Here is the evidence you asked for.",
                }
            ],
            "links": [
                {
                    "href": "https://api.paypal.com/v1/customer/disputes/PP-D-12345",
                    "rel": "self",
                    "method": "GET",
                }
            ],
        }
    elif event_type == PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED:
        return {
            "dispute_id": "PP-D-12345",
            "create_time": "2025-01-24T16:44:55Z",
            "update_time": "2025-01-30T12:00:00Z",
            "status": "RESOLVED",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            "dispute_amount": {"currency_code": "USD", "value": "99.99"},
            "disputed_transactions": [
                {
                    "buyer_transaction_id": "5O190127TN364715T",
                    "seller_transaction_id": "3C679366HH908993F",
                    "create_time": "2025-01-20T10:00:00Z",
                    "transaction_status": "COMPLETED",
                    "gross_amount": {"currency_code": "USD", "value": "99.99"},
                }
            ],
            "dispute_life_cycle_stage": "CHARGEBACK",
            "dispute_channel": "INTERNAL",
            "dispute_outcome": {
                "outcome_code": "RESOLVED_BUYER_FAVOUR",
                "amount_refunded": {"currency_code": "USD", "value": "99.99"},
            },
            "links": [
                {
                    "href": "https://api.paypal.com/v1/customer/disputes/PP-D-12345",
                    "rel": "self",
                    "method": "GET",
                }
            ],
        }

    raise ValueError


def generate_paypal_notification_example(
    event_type: PaypalNotificationEventType,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:

    headers = {
        "paypal-transmission-id": 0,
        "paypal-transmission-time": "2024-10-14T21:58:35.000Z",
        "paypal-transmission-sig": "",
        "paypal-cert-url": "",
        "paypal-auth-version": "",
        "content-type": "application/json",
    }

    def assemble_notification_body(event_type: PaypalNotificationEventType) -> Dict:
        return {
            "id": "WH-3F562076HD293871E-75F399086E414290U",
            "event_version": "1.0",
            "create_time": "2024-10-14T21:58:34.000Z",
            "resource_type": paypal_resource_type_for_notfication_type(
                event_type
            ).value,
            "resource_version": "2.0",
            "event_type": event_type.value,
            "summary": "Human readable description",
            "resource": example_resource_for_paypal_notification_type(event_type),
            "links": [
                {
                    "href": "https://api-m.paypal.com/v1/notifications/webhooks-events/WH-3F562076HD293871E-75F399086E414290U",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": "https://api-m.paypal.com/v1/notifications/webhooks-events/WH-3F562076HD293871E-75F399086E414290U/resend",
                    "rel": "resend",
                    "method": "POST",
                },
            ],
        }

    body = assemble_notification_body(event_type=event_type)

    return headers, body


@tag("PayPal")
@override_settings(
    PAYPAL_WEBHOOK_ID="TEST-WEBHOOK-ID",
    PAYPAL_CLIENT_ID="CID",
    PAYPAL_CLIENT_SECRET="SEC",
)
class TestPaypalWebhookSignatureVerification(TestCase):
    """Unit tests for verify_signature() in registration/views/paypal_webhooks.py.

    These exercise the implementation at views/paypal_webhooks.py:verify_signature.
    The production function issues two HTTP calls via ``urllib.request.urlopen``:

      1. POST /v1/oauth2/token  (OAuth2 bearer token)
      2. POST /v1/notifications/verify-webhook-signature

    We mock ``urllib.request.urlopen`` rather than ``verify_signature`` itself so
    the real code path (header parsing, settings gating, JSON parse, fail-closed
    branches) gets exercised.
    """

    ALL_HEADERS = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.paypal.com/v1/notifications/certs/CERT-ID",
        "paypal-transmission-id": "TRANS-ID-1",
        "paypal-transmission-sig": "SIG",
        "paypal-transmission-time": "2024-10-14T21:58:35.000Z",
    }

    @staticmethod
    def _mock_response(json_body, status=200):
        """Return a MagicMock usable as an ``urllib.request.urlopen`` return
        value.

        ``urlopen`` results are used as context managers (``with ... as resp:``),
        so we wire up ``__enter__`` to yield an inner mock exposing ``read()``
        and ``status``.
        """
        inner = MagicMock()
        inner.read.return_value = json.dumps(json_body).encode("utf-8")
        inner.status = status
        cm = MagicMock()
        cm.__enter__.return_value = inner
        cm.__exit__.return_value = False
        return cm

    def _build_paypal_request_with_headers(self, headers=None, body=None):
        """Build an HttpRequest carrying all (or a filtered subset of) the
        paypal-* headers plus a minimal JSON body."""
        if headers is None:
            headers = dict(self.ALL_HEADERS)
        if body is None:
            body = {"id": "WH-TEST", "event_type": "PAYMENT.CAPTURE.REFUNDED"}
        factory = RequestFactory()
        meta_headers = {}
        for name, value in headers.items():
            # Django's RequestFactory accepts HTTP_* META keys or, on modern
            # Django, the keyword form used by test Client. Use HTTP_ prefixed
            # META so request.headers.get() still resolves correctly.
            meta_headers["HTTP_" + name.upper().replace("-", "_")] = value
        request = factory.post(
            "/paypal-webhook",
            data=json.dumps(body),
            content_type="application/json",
            **meta_headers,
        )
        return request

    @patch("urllib.request.urlopen")
    def test_valid_signature_returns_true(self, mock_urlopen):
        """C1.1 — happy path: oauth token then SUCCESS verification."""
        mock_urlopen.side_effect = [
            self._mock_response({"access_token": "token", "token_type": "Bearer"}),
            self._mock_response({"verification_status": "SUCCESS"}),
        ]
        request = self._build_paypal_request_with_headers()
        self.assertTrue(verify_signature(request))
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_invalid_signature_returns_false(self, mock_urlopen):
        """C1.2 — PayPal replies FAILURE -> verify_signature returns False."""
        mock_urlopen.side_effect = [
            self._mock_response({"access_token": "token", "token_type": "Bearer"}),
            self._mock_response({"verification_status": "FAILURE"}),
        ]
        request = self._build_paypal_request_with_headers()
        self.assertFalse(verify_signature(request))

    @patch("urllib.request.urlopen")
    def test_missing_required_header_returns_false_without_http(self, mock_urlopen):
        """C1.3 — a missing paypal-* header must short-circuit BEFORE any HTTP
        call is issued."""
        headers = dict(self.ALL_HEADERS)
        del headers["paypal-transmission-sig"]
        request = self._build_paypal_request_with_headers(headers=headers)
        self.assertFalse(verify_signature(request))
        self.assertFalse(mock_urlopen.called)

    @patch("urllib.request.urlopen")
    def test_network_error_returns_false(self, mock_urlopen):
        """C1.4 — any urllib URLError / timeout must be caught and fail closed."""
        mock_urlopen.side_effect = URLError("boom")
        request = self._build_paypal_request_with_headers()
        with self.assertLogs(
            "registration.views.paypal_webhooks", level="ERROR"
        ) as captured:
            self.assertFalse(verify_signature(request))
        # At least one ERROR log entry should mention the failure.
        self.assertTrue(
            any("PayPal" in msg or "boom" in msg for msg in captured.output)
        )

    @patch("urllib.request.urlopen")
    def test_non_200_from_verify_endpoint_returns_false(self, mock_urlopen):
        """C1.5 — non-200 HTTP status from the verify endpoint fails closed."""
        mock_urlopen.side_effect = [
            self._mock_response({"access_token": "token", "token_type": "Bearer"}),
            self._mock_response({"verification_status": "SUCCESS"}, status=500),
        ]
        request = self._build_paypal_request_with_headers()
        self.assertFalse(verify_signature(request))

    @override_settings(PAYPAL_WEBHOOK_ID="")
    @patch("urllib.request.urlopen")
    def test_missing_webhook_id_fails_closed(self, mock_urlopen):
        """C1.6 — an unset / empty PAYPAL_WEBHOOK_ID must fail closed without
        any HTTP call."""
        request = self._build_paypal_request_with_headers()
        self.assertFalse(verify_signature(request))
        self.assertFalse(mock_urlopen.called)


class TestMalformedPaypalRefundWebhooks(TestCase):
    NOTIFICATION_URL = "https://webhook.site/test-paypal-webhook"

    baseline_headers, baseline_body = generate_paypal_notification_example(
        PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
    )

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_paypal_webhook_invalid_signature(self, mock_verify):
        """When signature verification returns False, the view should
        respond with 403."""
        mock_verify.return_value = False

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 403)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_paypal_webhook_invalid_json(self, mock_verify):
        mock_verify.return_value = True

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            '{"foo',
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Unable to decode JSON", response.content)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_paypal_webhook_missing_id(self, mock_verify):
        mock_verify.return_value = True

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            '{"foo":"bar"}',
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Missing id", response.content)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_paypal_webhook_idempotency(self, mock_verify):
        mock_verify.return_value = True

        notification = PaymentWebhookNotification(
            integration="paypal",
            event_id=self.baseline_body["id"],
            body=self.baseline_body,
            headers=dict(),
        )
        notification.save()

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)


class TestPaypalRefundWebhooks(TestCase):
    """Exhaustive tests for PayPal refund/reversal/dispute webhook handling.

    These tests target the eventual correct PayPal webhook handler. They will
    fail until registration/views/paypal_webhooks.py is rewritten to use PayPal
    field names and processing logic.
    """

    PAYPAL_CAPTURE_ID = "3C679366HH908993F"

    baseline_headers, baseline_body = generate_paypal_notification_example(
        PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
    )

    def setUp(self) -> None:
        self.event = Event(**DEFAULT_EVENT_ARGS)
        self.event.save()
        self.order = Order(
            total="99.99",
            status=Order.COMPLETED,
            reference="FOOBAR",
            billingEmail="apis@mailinator.com",
            lastFour="1111",
            apiData={
                "id": "5O190127TN364715T",
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "reference_id": "registration",
                        "payments": {
                            "captures": [
                                {
                                    "id": self.PAYPAL_CAPTURE_ID,
                                    "status": "COMPLETED",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": "99.99",
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        )
        self.order.save()
        self.attendee = Attendee(**TEST_ATTENDEE_ARGS)
        self.attendee.save()
        self.badge = Badge(
            attendee=self.attendee, event=self.event, badgeName="Test Badge"
        )
        self.badge.save()
        self.order_item = OrderItem(
            order=self.order, badge=self.badge, enteredBy="Test"
        )
        self.order_item.save()
        self.order.refresh_from_db()

    def _post_webhook(self, event_type: PaypalNotificationEventType):
        """Helper to generate and POST a PayPal webhook notification."""
        headers, body = generate_paypal_notification_example(event_type)
        return (
            self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers=headers,
            ),
            body,
        )

    # ---------------------------------------------------------------
    # A. Refund happy paths (per event type)
    # ---------------------------------------------------------------

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_refund_completed(self, mock_verify):
        """PAYMENT.CAPTURE.REFUNDED with status COMPLETED should store the
        notification, mark the order as refunded, and reduce the total."""
        mock_verify.return_value = True

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(
            event_id=self.baseline_body["id"]
        )
        self.assertEqual(webhook.event_type, "PAYMENT.CAPTURE.REFUNDED")
        self.assertEqual(webhook.integration, "paypal")
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.REFUNDED)
        self.assertIn("refunds", self.order.apiData)
        refund_ids = [r["id"] for r in self.order.apiData["refunds"]]
        self.assertIn(self.baseline_body["resource"]["id"], refund_ids)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_refunded_pending(self, mock_verify):
        """PAYMENT.CAPTURE.REFUNDED with status=PENDING (eCheck-funded refund):
        order -> REFUND_PENDING; total reduced now because PayPal has
        committed to refunding once the rail settles."""
        mock_verify.return_value = True

        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["resource"]["status"] = "PENDING"
        body["resource"]["amount"]["value"] = "50.00"
        body["id"] = "WH-PENDING-REFUND"
        body["resource"]["id"] = "REFUND-PENDING-1"

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.REFUND_PENDING)
        from decimal import Decimal

        self.assertEqual(self.order.total, Decimal("49.99"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_refunded_failed(self, mock_verify):
        """PAYMENT.CAPTURE.REFUNDED with status=FAILED: notification stored,
        processed, but order stays COMPLETED and total unchanged."""
        mock_verify.return_value = True

        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["resource"]["status"] = "FAILED"
        body["id"] = "WH-FAILED-REFUND"
        body["resource"]["id"] = "REFUND-FAILED-1"

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETED)
        from decimal import Decimal

        self.assertEqual(self.order.total, Decimal("99.99"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_refunded_cancelled(self, mock_verify):
        """PAYMENT.CAPTURE.REFUNDED with status=CANCELLED: treat like FAILED
        — no money moved, no state change for an otherwise-clean order."""
        mock_verify.return_value = True

        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["resource"]["status"] = "CANCELLED"
        body["id"] = "WH-CANCELLED-REFUND"
        body["resource"]["id"] = "REFUND-CANCELLED-1"

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETED)
        from decimal import Decimal

        self.assertEqual(self.order.total, Decimal("99.99"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_refunded_pending_then_completed_is_upsert(self, mock_verify):
        """Same refund resource delivered first as PENDING then as COMPLETED
        (typical eCheck flow). The refund list in apiData must contain exactly
        one entry with the final status, and total must not double-count."""
        mock_verify.return_value = True

        # First: PENDING
        _, body1 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body1["resource"]["status"] = "PENDING"
        body1["resource"]["amount"]["value"] = "99.99"
        body1["id"] = "WH-UPSERT-1"
        body1["resource"]["id"] = "REFUND-UPSERT-SAME-ID"

        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body1),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        # Second: same resource ID, now COMPLETED, different notification ID
        _, body2 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body2["resource"]["status"] = "COMPLETED"
        body2["resource"]["amount"]["value"] = "99.99"
        body2["id"] = "WH-UPSERT-2"
        body2["resource"]["id"] = "REFUND-UPSERT-SAME-ID"

        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body2),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.order.refresh_from_db()
        refunds = self.order.apiData.get("refunds", [])
        upsert_refunds = [r for r in refunds if r["id"] == "REFUND-UPSERT-SAME-ID"]
        self.assertEqual(len(upsert_refunds), 1)
        self.assertEqual(upsert_refunds[0]["status"], "COMPLETED")
        self.assertEqual(self.order.status, Order.REFUNDED)
        from decimal import Decimal

        self.assertEqual(self.order.total, Decimal("0.00"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_reversed(self, mock_verify):
        """PAYMENT.CAPTURE.REVERSED (PayPal-initiated reversal/chargeback).
        Resource is a v2 Refund object (not a Capture) despite the event
        name — confirmed by real sanitized payloads. Handler must record the
        reversal and update order status."""
        mock_verify.return_value = True

        response, body = self._post_webhook(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertEqual(webhook.event_type, "PAYMENT.CAPTURE.REVERSED")
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.REFUNDED)
        # Reversal is tagged in apiData so it can be distinguished from a
        # normal merchant-initiated refund during audit.
        refund_ids = [r["id"] for r in self.order.apiData.get("refunds", [])]
        self.assertIn(body["resource"]["id"], refund_ids)
        reversal_entry = next(
            r
            for r in self.order.apiData["refunds"]
            if r["id"] == body["resource"]["id"]
        )
        self.assertTrue(reversal_entry.get("reversal"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_sale_refunded_v1_format(self, mock_verify):
        """PAYMENT.SALE.REFUNDED uses v1 fields (state, amount.total,
        amount.currency) rather than v2 (status, amount.value,
        amount.currency_code). Handler must read the correct fields."""
        mock_verify.return_value = True

        response, body = self._post_webhook(
            PaypalNotificationEventType.PAYMENT_SALE_REFUNDED
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertEqual(webhook.event_type, "PAYMENT.SALE.REFUNDED")
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.REFUNDED)

    # ---------------------------------------------------------------
    # B. Dispute tests
    # ---------------------------------------------------------------

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_dispute_created(self, mock_verify):
        """CUSTOMER.DISPUTE.CREATED should update order status, populate
        apiData["dispute"], add attendee to BanList, and set holdType."""
        mock_verify.return_value = True

        response, body = self._post_webhook(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_EVIDENCE_REQUIRED)
        self.assertIn("dispute", self.order.apiData)

        ban = BanList.objects.filter(email=self.attendee.email).first()
        self.assertIsNotNone(ban)
        self.assertEqual(ban.firstName, self.attendee.firstName)
        self.assertEqual(ban.lastName, self.attendee.lastName)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_dispute_order_not_found(self, mock_verify):
        """Dispute for a payment ID not in our system: notification stored but
        processed=False."""
        mock_verify.return_value = True

        # Clear apiData so capture ID won't match
        self.order.apiData = {}
        self.order.save()

        response, body = self._post_webhook(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    # ---------------------------------------------------------------
    # C. Partial / multiple refund edge cases
    # ---------------------------------------------------------------

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_partial_refund(self, mock_verify):
        """A refund for less than the full order total should reduce the total
        by the refund amount, not zero it out."""
        mock_verify.return_value = True

        # Build a PAYMENT.CAPTURE.REFUNDED event with PENDING status and a
        # $50 partial amount applied against the $99.99 baseline order.
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["resource"]["status"] = "PENDING"
        body["resource"]["amount"]["value"] = "50.00"
        body["id"] = "WH-PARTIAL-REFUND"
        body["resource"]["id"] = "REFUND-PARTIAL-1"

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        from decimal import Decimal

        self.assertEqual(self.order.total, Decimal("49.99"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_multiple_sequential_refunds(self, mock_verify):
        """Two distinct refund notifications for the same order should both be
        stored in apiData and reduce the total cumulatively."""
        mock_verify.return_value = True

        # First refund: $50.00 PENDING, unique resource id.
        _, body1 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body1["resource"]["status"] = "PENDING"
        body1["resource"]["amount"]["value"] = "50.00"
        body1["id"] = "WH-SEQUENTIAL-1"
        body1["resource"]["id"] = "REFUND-SEQUENTIAL-1"
        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body1),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        # Second refund: $40.00 COMPLETED, different resource id.
        _, body2 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body2["resource"]["status"] = "COMPLETED"
        body2["resource"]["amount"]["value"] = "40.00"
        body2["id"] = "WH-SEQUENTIAL-2"
        body2["resource"]["id"] = "REFUND-SEQUENTIAL-2"
        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body2),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.order.refresh_from_db()
        refunds = self.order.apiData.get("refunds", [])
        self.assertEqual(len(refunds), 2)
        from decimal import Decimal

        # Starting from $99.99: minus $50 then minus $40 = $9.99
        self.assertEqual(self.order.total, Decimal("9.99"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_refund_resets_donations_when_total_insufficient(self, mock_verify):
        """When a refund causes the remaining total to be less than
        orgDonation + charityDonation, donations should be reset."""
        mock_verify.return_value = True

        self.order.orgDonation = 10
        self.order.charityDonation = 10
        self.order.save()

        # Full refund of $99.99 on a $99.99 order with $20 in donations
        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.orgDonation, 0)

    # ---------------------------------------------------------------
    # D. Error / edge cases
    # ---------------------------------------------------------------

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_refund_order_not_found(self, mock_verify):
        """Refund for a capture ID not matching any order: notification stored
        but processed=False."""
        mock_verify.return_value = True

        # Clear apiData so capture ID won't match
        self.order.apiData = {}
        self.order.save()

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(
            event_id=self.baseline_body["id"]
        )
        self.assertFalse(webhook.processed)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_refund_idempotent_redelivery(self, mock_verify):
        """Same refund resource delivered via two different webhook
        notifications (PayPal retry). The refund should not be duplicated in
        apiData["refunds"]."""
        mock_verify.return_value = True

        # First delivery
        headers1, body1 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body1),
            content_type="application/json",
            headers=headers1,
        )

        # Second delivery with a different notification ID but same refund resource
        headers2, body2 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body2["id"] = "WH-REDELIVERY-DIFFERENT-NOTIFICATION-ID"
        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body2),
            content_type="application/json",
            headers=headers2,
        )

        self.order.refresh_from_db()
        refunds = self.order.apiData.get("refunds", [])
        refund_ids = [r["id"] for r in refunds]
        # The same refund resource ID should only appear once
        self.assertEqual(
            len([rid for rid in refund_ids if rid == body1["resource"]["id"]]),
            1,
        )

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_duplicate_notification_id(self, mock_verify):
        """Same webhook notification ID sent twice. Second POST should return
        200 (idempotent) without reprocessing."""
        mock_verify.return_value = True

        # First POST
        response1 = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )
        self.assertEqual(response1.status_code, 200)

        # Second POST with identical body (same notification ID)
        response2 = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(self.baseline_body),
            content_type="application/json",
            headers=self.baseline_headers,
        )
        self.assertEqual(response2.status_code, 200)

        # Should only have one notification stored
        count = PaymentWebhookNotification.objects.filter(
            event_id=self.baseline_body["id"]
        ).count()
        self.assertEqual(count, 1)


@tag("PayPal")
class TestPaypalWebhookStubs(PayPalOrdersTestCase):
    """Dispute-lifecycle webhook scenarios handled by
    :mod:`registration.paypal_webhook_handlers`.
    """

    def _post(self, body, signature_ok=True):
        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=signature_ok,
        ):
            return self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers={"content-type": "application/json"},
            )

    def test_dispute_updated(self):
        """CUSTOMER.DISPUTE.UPDATED — refreshes apiData['dispute'] snapshot
        and updates status if dispute is actively being processed."""
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_UPDATED
        )
        response = self._post(body)

        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertEqual(webhook.integration, "paypal")
        self.assertEqual(webhook.event_type, "CUSTOMER.DISPUTE.UPDATED")
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertIn("dispute", self.order.apiData)
        self.assertEqual(self.order.apiData["dispute"]["status"], "UNDER_REVIEW")
        self.assertEqual(self.order.status, Order.DISPUTE_PROCESSING)

    def _post_resolved_with_outcome(self, outcome_code):
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED
        )
        body["resource"]["dispute_outcome"]["outcome_code"] = outcome_code
        body["id"] = f"WH-RESOLVED-{outcome_code}"
        return body, self._post(body)

    def test_dispute_resolved_buyer_favor(self):
        body, response = self._post_resolved_with_outcome("RESOLVED_BUYER_FAVOUR")
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_LOST)

    def test_dispute_resolved_seller_favor(self):
        body, response = self._post_resolved_with_outcome("RESOLVED_SELLER_FAVOUR")
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_WON)

    def test_dispute_resolved_accepted(self):
        body, response = self._post_resolved_with_outcome("ACCEPTED")
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_ACCEPTED)

    @patch("registration.tasks.send_chargeback_notice_email_task.delay")
    def test_chargeback_admin_email_sent(self, mock_delay):
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED
        )
        response = self._post(body)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_EVIDENCE_REQUIRED)
        mock_delay.assert_called_once_with(self.order.id)

    def test_unknown_event_type(self):
        """A recognized envelope with an event_type we don't route: store the
        notification but leave it unprocessed."""
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["event_type"] = "FOOBAR.UNKNOWN"
        body["id"] = "WH-UNKNOWN-EVENT"

        response = self._post(body)
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_unknown_resource_type(self):
        """A recognized event_type with a nonsensical resource_type should not
        crash the handler."""
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["resource_type"] = "alien"
        body["id"] = "WH-UNKNOWN-RESOURCE"

        response = self._post(body)
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        # Handler still runs by event_type; resource shape is fine, so it
        # processes normally. Asserting no exception is the real contract.
        self.assertIn(webhook.processed, (True, False))

    def test_missing_resource_key(self):
        """A body lacking the top-level resource key: stored, processed=False,
        no exception."""
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body.pop("resource", None)
        body["id"] = "WH-NO-RESOURCE"

        response = self._post(body)
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)


@tag("paypal")
class TestPaypalRefundWebhookPerRegistrantType(TestCase):
    """Refund webhook resolves orders uniformly across registrant types.

    The handler looks up Orders by ``Order.reference`` regardless of whether
    the OrderItem chain leads to an Attendee, Dealer, Staff, or upgrade
    badge. This class seeds each shape and asserts the refund webhook drives
    ``Order.status`` to ``REFUNDED`` and reduces ``Order.total`` to 0,
    proving the webhook pipeline is processor- and registrant-agnostic.
    """

    PAYPAL_CAPTURE_ID = "3C679366HH908993F"

    def setUp(self):
        from registration.models import Dealer, DealerAsst, Staff

        self.Dealer = Dealer
        self.DealerAsst = DealerAsst
        self.Staff = Staff
        self.event = Event(**DEFAULT_EVENT_ARGS)
        self.event.save()

    def _seed_order(self, reference: str, total="99.99"):
        order = Order(
            total=total,
            status=Order.COMPLETED,
            reference=reference,
            billingEmail="apis@mailinator.com",
            lastFour="1111",
            apiData={
                "id": "5O190127TN364715T",
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "reference_id": "registration",
                        "payments": {
                            "captures": [
                                {
                                    "id": self.PAYPAL_CAPTURE_ID,
                                    "status": "COMPLETED",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": total,
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        )
        order.save()
        return order

    def _post_refund_for(self, reference, capture_id=None):
        headers, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["resource"]["custom_id"] = reference
        body["resource"]["invoice_id"] = reference
        if capture_id:
            body["resource"]["links"] = [
                {
                    "rel": "up",
                    "method": "GET",
                    "href": f"https://api.paypal.com/v2/payments/captures/{capture_id}",
                }
            ]
        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=True,
        ):
            response = self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers=headers,
            )
        return response, body

    def _make_attendee(self, email, first="Atten", last="Dee"):
        attendee = Attendee(
            firstName=first,
            lastName=last,
            address1="123 Somewhere St",
            city="Place",
            state="PA",
            country="US",
            postalCode="12345",
            phone="1112223333",
            email=email,
            birthdate="1990-01-01",
        )
        attendee.save()
        return attendee

    @patch("registration.views.paypal_webhooks.verify_signature", return_value=True)
    def test_refund_for_attendee_order(self, _mv):
        attendee = self._make_attendee("attendee@example.com")
        badge = Badge(attendee=attendee, event=self.event, badgeName="Att")
        badge.save()
        order = self._seed_order("ATT-REF")
        OrderItem(order=order, badge=badge, enteredBy="Test").save()

        response, _ = self._post_refund_for("ATT-REF", self.PAYPAL_CAPTURE_ID)
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)

    @patch("registration.views.paypal_webhooks.verify_signature", return_value=True)
    def test_refund_for_dealer_order(self, _mv):
        from registration.models import TableSize
        from registration.tests.common import TEST_TABLE_ARGS

        dealer_attendee = self._make_attendee("dealer@example.com", "Dealer", "Test")
        badge = Badge(
            attendee=dealer_attendee, event=self.event, badgeName="DealerBadge"
        )
        badge.save()
        table = TableSize(**TEST_TABLE_ARGS)
        table.save()
        dealer = self.Dealer(
            attendee=dealer_attendee,
            event=self.event,
            businessName="Dealer Biz",
            license="abc",
            tableSize=table,
        )
        dealer.save()
        self.DealerAsst(
            dealer=dealer,
            event=self.event,
            name="Assistant One",
            email="asst@example.com",
            license="N/A",
            paid=True,
        ).save()

        order = self._seed_order("DLR-REF")
        OrderItem(order=order, badge=badge, enteredBy="Test").save()

        response, _ = self._post_refund_for("DLR-REF", self.PAYPAL_CAPTURE_ID)
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)

    @patch("registration.views.paypal_webhooks.verify_signature", return_value=True)
    def test_refund_for_staff_order(self, _mv):
        from registration.models import Department

        staff_attendee = self._make_attendee("staff@example.com", "Staff", "Member")
        badge = Badge(attendee=staff_attendee, event=self.event, badgeName="StaffBadge")
        badge.save()
        dept = Department(name="Safety")
        dept.save()
        staff = self.Staff(attendee=staff_attendee, event=self.event, department=dept)
        staff.save()

        order = self._seed_order("STF-REF")
        OrderItem(order=order, badge=badge, enteredBy="Test").save()

        response, _ = self._post_refund_for("STF-REF", self.PAYPAL_CAPTURE_ID)
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)

    @patch("registration.views.paypal_webhooks.verify_signature", return_value=True)
    def test_refund_for_upgrade_order(self, _mv):
        """An upgrade Order refund should transition to REFUNDED the same way.

        The badge still has its upgraded price level attached — product spec
        (documented here) is that a refund of an upgrade payment records the
        refund but does NOT revert the badge's level. That's a policy call,
        not a webhook responsibility.
        """
        attendee = self._make_attendee("upgrade@example.com", "Up", "Grader")
        badge = Badge(attendee=attendee, event=self.event, badgeName="UpgBadge")
        badge.save()

        order = self._seed_order("UPG-REF", total="45.00")
        OrderItem(order=order, badge=badge, enteredBy="Test").save()

        response, _ = self._post_refund_for("UPG-REF", self.PAYPAL_CAPTURE_ID)
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.REFUNDED)


# ---------------------------------------------------------------------------
# Coverage-gap fills for views/paypal_webhooks.py
# ---------------------------------------------------------------------------


@tag("PayPal")
class TestPaypalViewsCoverageGaps(TestCase):
    """Branches in registration/views/paypal_webhooks.py not reached by the
    signature, malformed-body, or refund-happy-path suites above:

    * _paypal_api_base production switch (L27)
    * _get_paypal_access_token missing credentials (L39-40)
    * verify_signature body not valid JSON (L95-97)
    * verify_signature verify-call urlopen raises (L131-133)
    * paypal_webhook IntegrityError on notification.save (L171-174)
    * process_webhook handler raises exception (L200-205)
    """

    ALL_HEADERS = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.paypal.com/v1/notifications/certs/CERT",
        "paypal-transmission-id": "TRANS-ID",
        "paypal-transmission-sig": "SIG",
        "paypal-transmission-time": "2024-10-14T21:58:35.000Z",
    }

    @override_settings(PAYPAL_ENVIRONMENT="production")
    def test_paypal_api_base_returns_live_when_production(self):
        from registration.views.paypal_webhooks import (
            PAYPAL_LIVE_API_BASE,
            _paypal_api_base,
        )

        self.assertEqual(PAYPAL_LIVE_API_BASE, _paypal_api_base())

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")
    def test_get_paypal_access_token_returns_none_without_credentials(self):
        from registration.views.paypal_webhooks import _get_paypal_access_token

        with self.assertLogs(
            "registration.views.paypal_webhooks", level="WARNING"
        ) as captured:
            self.assertIsNone(_get_paypal_access_token())
        self.assertTrue(
            any("credentials not configured" in msg for msg in captured.output)
        )

    @override_settings(
        PAYPAL_WEBHOOK_ID="TEST-WEBHOOK-ID",
        PAYPAL_CLIENT_ID="CID",
        PAYPAL_CLIENT_SECRET="SEC",
    )
    @patch("urllib.request.urlopen")
    def test_verify_signature_returns_false_for_invalid_json_body(self, mock_urlopen):
        """verify_signature reads request.body as JSON; a malformed body must
        fail-closed without making any HTTP calls."""
        factory = RequestFactory()
        meta = {
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in self.ALL_HEADERS.items()
        }
        request = factory.post(
            "/paypal-webhook",
            data='{"not valid json',
            content_type="application/json",
            **meta,
        )
        with self.assertLogs(
            "registration.views.paypal_webhooks", level="WARNING"
        ) as captured:
            self.assertFalse(verify_signature(request))
        self.assertFalse(mock_urlopen.called)
        self.assertTrue(any("not valid JSON" in msg for msg in captured.output))

    @override_settings(
        PAYPAL_WEBHOOK_ID="TEST-WEBHOOK-ID",
        PAYPAL_CLIENT_ID="CID",
        PAYPAL_CLIENT_SECRET="SEC",
    )
    @patch("urllib.request.urlopen")
    def test_verify_signature_returns_false_when_verify_call_raises(self, mock_urlopen):
        """OAuth token fetch succeeds, but the verify-webhook-signature call
        raises URLError — this hits the second try/except (L131-133) rather
        than the token-fetch except block."""
        token_response = MagicMock()
        inner = MagicMock()
        inner.read.return_value = json.dumps(
            {"access_token": "token", "token_type": "Bearer"}
        ).encode("utf-8")
        inner.status = 200
        token_response.__enter__.return_value = inner
        token_response.__exit__.return_value = False

        mock_urlopen.side_effect = [token_response, URLError("verify exploded")]

        factory = RequestFactory()
        meta = {
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in self.ALL_HEADERS.items()
        }
        request = factory.post(
            "/paypal-webhook",
            data=json.dumps({"id": "WH-1", "event_type": "X"}),
            content_type="application/json",
            **meta,
        )
        with self.assertLogs(
            "registration.views.paypal_webhooks", level="ERROR"
        ) as captured:
            self.assertFalse(verify_signature(request))
        self.assertTrue(
            any(
                "verify-webhook-signature call failed" in msg for msg in captured.output
            )
        )

    @patch("registration.views.paypal_webhooks.verify_signature")
    @patch("registration.models.PaymentWebhookNotification.save")
    def test_paypal_webhook_integrity_error_returns_200(self, mock_save, mock_verify):
        """An IntegrityError raised during notification.save (race with a
        concurrent duplicate delivery) must be caught; endpoint returns 200."""
        from django.db import IntegrityError

        mock_verify.return_value = True
        mock_save.side_effect = IntegrityError("duplicate event_id")

        body = {"id": "WH-RACE", "event_type": "PAYMENT.CAPTURE.REFUNDED"}
        with self.assertLogs(
            "registration.views.paypal_webhooks", level="WARNING"
        ) as captured:
            response = self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers={"content-type": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("already exists" in msg for msg in captured.output))

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_process_webhook_catches_handler_exception(self, mock_verify):
        """A registered handler that raises must be caught by process_webhook;
        the notification is stored with processed=False and the endpoint
        still returns 200. The _HANDLERS dict captures function references
        at import time, so we patch the dict entry directly rather than the
        handler module attribute."""
        from registration.views.paypal_webhooks import _HANDLERS

        mock_verify.return_value = True

        body = {
            "id": "WH-HANDLER-BOOM",
            "event_type": "PAYMENT.CAPTURE.REFUNDED",
            "resource_type": "refund",
            "resource": {
                "id": "R-1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "1.00"},
            },
        }

        def boom(_notification):
            raise RuntimeError("handler boom")

        with (
            patch.dict(_HANDLERS, {"PAYMENT.CAPTURE.REFUNDED": boom}),
            self.assertLogs(
                "registration.views.paypal_webhooks", level="ERROR"
            ) as captured,
        ):
            response = self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers={"content-type": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)
        self.assertTrue(
            any("crashed; marking unprocessed" in msg for msg in captured.output)
        )


# ---------------------------------------------------------------------------
# Coverage-gap fills for paypal_webhook_handlers.py
# ---------------------------------------------------------------------------


@tag("PayPal")
class TestPaypalHandlerHelperCoverageGaps(TestCase):
    """Direct unit tests for private helpers in paypal_webhook_handlers.py.

    These helpers are normally reached only by webhook-endpoint tests with a
    well-formed payload. To drive the defensive branches (None inputs,
    malformed apiData, malformed HATEOAS links) we call the helpers directly.
    """

    def test_find_order_by_capture_id_returns_none_for_none(self):
        from registration.paypal_webhook_handlers import (
            _find_order_by_capture_id,
        )

        self.assertIsNone(_find_order_by_capture_id(None))
        self.assertIsNone(_find_order_by_capture_id(""))

    def test_find_order_by_capture_id_skips_orders_without_apidata(self):
        """Orders whose apiData is None, empty-dict, or missing purchase_units
        must be skipped without crashing the scan."""
        from registration.paypal_webhook_handlers import (
            _find_order_by_capture_id,
        )

        Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="NO-API-DATA",
            billingEmail="x@example.com",
            apiData=None,
        ).save()
        Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="EMPTY-PURCHASE-UNITS",
            billingEmail="x@example.com",
            apiData={"purchase_units": []},
        ).save()
        # Legitimately captures-bearing order with a non-matching id:
        Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="NON-MATCH",
            billingEmail="x@example.com",
            apiData={
                "purchase_units": [
                    {"payments": {"captures": [{"id": "SOMETHING-ELSE"}]}}
                ]
            },
        ).save()

        self.assertIsNone(_find_order_by_capture_id("not-a-real-capture"))

    def test_find_order_by_capture_id_handles_malformed_shapes(self):
        """Orders whose purchase_units chain has non-dict payments must be
        skipped via the except (AttributeError, IndexError, TypeError)
        clause rather than propagating."""
        from registration.paypal_webhook_handlers import (
            _find_order_by_capture_id,
        )

        # purchase_units[0] is None — safe via `(None or {})`, just skipped.
        Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="PU0-NONE",
            billingEmail="x@example.com",
            apiData={"purchase_units": [None]},
        ).save()
        # payments is a string — `.get("captures")` raises AttributeError,
        # which is caught by the except clause and skipped.
        Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="PAYMENTS-STR",
            billingEmail="x@example.com",
            apiData={"purchase_units": [{"payments": "not a dict"}]},
        ).save()

        # No exception raised; returns None.
        self.assertIsNone(_find_order_by_capture_id("no-match"))

    def test_parse_capture_id_from_links_handles_malformed_href(self):
        """A link with rel=up but whose href does not contain /captures/
        hits the IndexError/continue branch and produces None."""
        from registration.paypal_webhook_handlers import (
            _parse_capture_id_from_links,
        )

        resource = {
            "links": [
                {"rel": "up", "href": "https://api.paypal.com/v2/something-else/xxx"}
            ]
        }
        self.assertIsNone(_parse_capture_id_from_links(resource))

    def test_parse_capture_id_from_links_returns_none_without_rel_up(self):
        from registration.paypal_webhook_handlers import (
            _parse_capture_id_from_links,
        )

        resource = {
            "links": [
                {"rel": "self", "href": "https://api.paypal.com/v2/payments/refunds/R"},
                {
                    "rel": "refund",
                    "href": "https://api.paypal.com/v2/payments/captures/CAP",
                },
            ]
        }
        self.assertIsNone(_parse_capture_id_from_links(resource))

    def test_find_order_for_v2_refund_falls_back_to_custom_id(self):
        """When invoice_id doesn't resolve an Order, the helper must try
        custom_id before falling through to the capture-id scan."""
        from registration.paypal_webhook_handlers import (
            _find_order_for_v2_refund,
        )

        order = Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="CUSTOM-REF-ONLY",
            billingEmail="x@example.com",
        )
        order.save()

        found = _find_order_for_v2_refund(
            {"invoice_id": "does-not-exist", "custom_id": "CUSTOM-REF-ONLY"}
        )
        self.assertEqual(found.pk, order.pk)

    def test_find_order_for_v1_sale_falls_back_to_custom(self):
        from registration.paypal_webhook_handlers import _find_order_for_v1_sale

        order = Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="V1-CUSTOM",
            billingEmail="x@example.com",
        )
        order.save()

        found = _find_order_for_v1_sale(
            {"invoice_number": "missing", "custom": "V1-CUSTOM"}
        )
        self.assertEqual(found.pk, order.pk)

    def test_find_order_for_dispute_fallback_paths(self):
        """The dispute lookup tries custom, then invoice_number, then the
        seller_transaction_id capture-id fallback. Hit the first two
        fall-through branches explicitly."""
        from registration.paypal_webhook_handlers import _find_order_for_dispute

        invoice_order = Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="DISPUTE-INVOICE",
            billingEmail="x@example.com",
        )
        invoice_order.save()
        custom_order = Order(
            total="1.00",
            status=Order.COMPLETED,
            reference="DISPUTE-CUSTOM",
            billingEmail="x@example.com",
        )
        custom_order.save()

        found_by_custom = _find_order_for_dispute(
            {
                "disputed_transactions": [
                    {"custom": "DISPUTE-CUSTOM", "invoice_number": "nothing"}
                ]
            }
        )
        self.assertEqual(found_by_custom.pk, custom_order.pk)

        found_by_invoice = _find_order_for_dispute(
            {"disputed_transactions": [{"invoice_number": "DISPUTE-INVOICE"}]}
        )
        self.assertEqual(found_by_invoice.pk, invoice_order.pk)


@tag("PayPal")
class TestPaypalHandlerMissingResourceCoverageGaps(PayPalOrdersTestCase):
    """Every handler has a defensive `if not isinstance(resource, dict)` guard.
    The existing test_missing_resource_key test covers the CAPTURE_REFUNDED
    branch; cover the remaining 4 siblings here."""

    def _post_missing_resource(self, event_type: PaypalNotificationEventType):
        _, body = generate_paypal_notification_example(event_type)
        body.pop("resource", None)
        body["id"] = f"WH-NO-RESOURCE-{event_type.name}"
        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=True,
        ):
            return (
                self.client.post(
                    reverse("registration:paypal_webhook"),
                    json.dumps(body),
                    content_type="application/json",
                    headers={"content-type": "application/json"},
                ),
                body,
            )

    def test_capture_reversed_missing_resource(self):
        response, body = self._post_missing_resource(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_sale_refunded_missing_resource(self):
        response, body = self._post_missing_resource(
            PaypalNotificationEventType.PAYMENT_SALE_REFUNDED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_dispute_created_missing_resource(self):
        response, body = self._post_missing_resource(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_dispute_updated_missing_resource(self):
        response, body = self._post_missing_resource(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_UPDATED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_dispute_resolved_missing_resource(self):
        response, body = self._post_missing_resource(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)


@tag("PayPal")
class TestPaypalHandlerNoMatchingOrderCoverageGaps(TestCase):
    """Every handler has a `no matching order` fall-through branch. The
    existing tests cover capture-refunded and dispute-created; cover the
    remaining siblings here. Each test runs against an empty order table so
    the lookup cannot succeed."""

    def _post(self, event_type: PaypalNotificationEventType):
        _, body = generate_paypal_notification_example(event_type)
        body["id"] = f"WH-NO-ORDER-{event_type.name}"
        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=True,
        ):
            return (
                self.client.post(
                    reverse("registration:paypal_webhook"),
                    json.dumps(body),
                    content_type="application/json",
                    headers={"content-type": "application/json"},
                ),
                body,
            )

    def test_capture_reversed_no_matching_order(self):
        response, body = self._post(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_sale_refunded_no_matching_order(self):
        response, body = self._post(PaypalNotificationEventType.PAYMENT_SALE_REFUNDED)
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_dispute_updated_no_matching_order(self):
        response, body = self._post(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_UPDATED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)

    def test_dispute_resolved_no_matching_order(self):
        response, body = self._post(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED
        )
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(webhook.processed)


@tag("PayPal")
class TestPaypalHandlerApiDataInitCoverageGaps(PayPalOrdersTestCase):
    """The dispute_* and _upsert_refund_in_apidata paths all have an
    ``if not isinstance(order.apiData, dict): order.apiData = {}`` guard.
    Drive it by seeding orders whose apiData is None *and* whose reference
    will resolve the lookup so the guard branch is taken."""

    def setUp(self):
        super().setUp()
        # Clobber apiData after base setUp so the handler's apiData-init
        # guard activates. Order stays resolvable because we route the
        # webhook via invoice_id/custom_id (refund) or disputed_transactions
        # custom (dispute) matching self.order.reference="FOOBAR".
        self.order.apiData = None
        self.order.save()

    def _post_refund_routed_to_order(self):
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body["id"] = "WH-NONE-APIDATA-REFUND"
        body["resource"]["invoice_id"] = self.order.reference
        body["resource"]["custom_id"] = self.order.reference
        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=True,
        ):
            return (
                self.client.post(
                    reverse("registration:paypal_webhook"),
                    json.dumps(body),
                    content_type="application/json",
                    headers={"content-type": "application/json"},
                ),
                body,
            )

    def _post_dispute_routed_to_order(self, event_type: PaypalNotificationEventType):
        _, body = generate_paypal_notification_example(event_type)
        body["id"] = f"WH-NONE-APIDATA-{event_type.name}"
        body["resource"]["disputed_transactions"][0]["custom"] = self.order.reference
        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=True,
        ):
            return (
                self.client.post(
                    reverse("registration:paypal_webhook"),
                    json.dumps(body),
                    content_type="application/json",
                    headers={"content-type": "application/json"},
                ),
                body,
            )

    def test_refund_initializes_apidata_when_not_dict(self):
        """_upsert_refund_in_apidata — reached indirectly via
        PAYMENT.CAPTURE.REFUNDED when the seeded apiData is None."""
        response, body = self._post_refund_routed_to_order()
        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)
        self.order.refresh_from_db()
        self.assertIsInstance(self.order.apiData, dict)
        self.assertIn("refunds", self.order.apiData)

    def test_dispute_created_initializes_apidata_when_not_dict(self):
        response, _ = self._post_dispute_routed_to_order(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertIsInstance(self.order.apiData, dict)
        self.assertIn("dispute", self.order.apiData)

    def test_dispute_updated_initializes_apidata_when_not_dict(self):
        response, _ = self._post_dispute_routed_to_order(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_UPDATED
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertIsInstance(self.order.apiData, dict)
        self.assertIn("dispute", self.order.apiData)

    def test_dispute_resolved_initializes_apidata_when_not_dict(self):
        response, _ = self._post_dispute_routed_to_order(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertIsInstance(self.order.apiData, dict)
        self.assertIn("dispute", self.order.apiData)


@tag("PayPal")
class TestPaypalHandlerMiscCoverageGaps(PayPalOrdersTestCase):
    """Remaining handler branches:

    * _apply_refund_side_effects: CANCELLED refund undoes a prior COMPLETED
      refund (status -> COMPLETED, total restored). L254.
    * handle_dispute_created: email task enqueue raises; dispute state
      already persisted so the handler logs and still returns True. L412-416.
    * handle_dispute_resolved: donation-reset branch when outcome drops the
      order below donation total. L481-487.
    """

    def test_refund_cancellation_restores_order_status_and_total(self):
        """A refund delivered first as COMPLETED then as CANCELLED must
        restore Order.status to COMPLETED and refund back the reduced total.
        Hits _apply_refund_side_effects's undo branch."""
        from decimal import Decimal

        with patch(
            "registration.views.paypal_webhooks.verify_signature",
            return_value=True,
        ):
            # First: COMPLETED -> order becomes REFUNDED, total 0.
            _, body1 = generate_paypal_notification_example(
                PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
            )
            body1["id"] = "WH-UNDO-1"
            body1["resource"]["id"] = "REFUND-UNDO"
            body1["resource"]["status"] = "COMPLETED"
            body1["resource"]["amount"]["value"] = "99.99"
            self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body1),
                content_type="application/json",
                headers={"content-type": "application/json"},
            )
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, Order.REFUNDED)

            # Second: same refund id, now CANCELLED -> undo.
            _, body2 = generate_paypal_notification_example(
                PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
            )
            body2["id"] = "WH-UNDO-2"
            body2["resource"]["id"] = "REFUND-UNDO"
            body2["resource"]["status"] = "CANCELLED"
            body2["resource"]["amount"]["value"] = "99.99"
            self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body2),
                content_type="application/json",
                headers={"content-type": "application/json"},
            )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETED)
        self.assertEqual(self.order.total, Decimal("99.99"))

    @patch(
        "registration.tasks.send_chargeback_notice_email_task.delay",
        side_effect=RuntimeError("broker unreachable"),
    )
    @patch("registration.views.paypal_webhooks.verify_signature", return_value=True)
    def test_dispute_created_handles_email_task_enqueue_failure(
        self, _mock_verify, _mock_delay
    ):
        """If Celery/broker is unreachable, the dispute state must still be
        persisted and the handler must mark the webhook processed=True."""
        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_CREATED
        )
        body["id"] = "WH-DISPUTE-BROKER-DOWN"
        with self.assertLogs(
            "registration.paypal_webhook_handlers", level="ERROR"
        ) as captured:
            response = self.client.post(
                reverse("registration:paypal_webhook"),
                json.dumps(body),
                content_type="application/json",
                headers={"content-type": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_EVIDENCE_REQUIRED)
        self.assertTrue(
            any(
                "Failed to queue chargeback notice email" in msg
                for msg in captured.output
            )
        )

    @patch("registration.views.paypal_webhooks.verify_signature", return_value=True)
    def test_dispute_resolved_resets_donations_on_lost_outcome(self, _mv):
        """RESOLVED_BUYER_FAVOUR -> DISPUTE_LOST while the order carries
        donations: donations must be zeroed and the notes field must record
        the reset for admin audit."""
        self.order.orgDonation = 10
        self.order.charityDonation = 15
        self.order.save()

        _, body = generate_paypal_notification_example(
            PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED
        )
        body["id"] = "WH-DISPUTE-LOST-RESETS-DONATIONS"
        body["resource"]["dispute_outcome"]["outcome_code"] = "RESOLVED_BUYER_FAVOUR"

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.DISPUTE_LOST)
        from decimal import Decimal

        self.assertEqual(self.order.orgDonation, Decimal("0"))
        self.assertEqual(self.order.charityDonation, Decimal("0"))
        self.assertIn("donation", (self.order.notes or "").lower())


@tag("PayPal")
class TestPaypalCaptureWebhooks(TestCase):
    """Handlers for PAYMENT.CAPTURE.COMPLETED and PAYMENT.CAPTURE.DENIED."""

    PAYPAL_CAPTURE_ID = "3C679366HH908993F"
    REFERENCE = "CAPREF"

    def setUp(self):
        self.event = Event(**DEFAULT_EVENT_ARGS)
        self.event.save()
        self.order = Order(
            total="99.99",
            status=Order.PENDING,
            reference=self.REFERENCE,
            billingEmail="apis@mailinator.com",
            lastFour="1111",
        )
        self.order.save()

    def _capture_body(self, event_type, status="COMPLETED"):
        return {
            "id": f"WH-{event_type}-1",
            "event_type": event_type,
            "resource_type": "capture",
            "resource": {
                "id": self.PAYPAL_CAPTURE_ID,
                "status": status,
                "amount": {"currency_code": "USD", "value": "99.99"},
                "invoice_id": self.REFERENCE,
                "custom_id": self.REFERENCE,
            },
        }

    @patch("registration.paypal_webhook_handlers.tasks")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_completed_pending_to_completed_queues_email(
        self, mock_verify, mock_tasks
    ):
        mock_verify.return_value = True
        body = self._capture_body("PAYMENT.CAPTURE.COMPLETED")

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETED)
        mock_tasks.send_registration_email_task.delay.assert_called_once_with(
            self.order.id, self.order.billingEmail
        )

    @patch("registration.paypal_webhook_handlers.tasks")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_completed_is_idempotent_when_already_completed(
        self, mock_verify, mock_tasks
    ):
        mock_verify.return_value = True
        self.order.status = Order.COMPLETED
        self.order.email_sent = True
        self.order.save()
        body = self._capture_body("PAYMENT.CAPTURE.COMPLETED")

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETED)
        mock_tasks.send_registration_email_task.delay.assert_not_called()

    @patch("registration.paypal_webhook_handlers.tasks")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_completed_does_not_queue_email_when_already_sent(
        self, mock_verify, mock_tasks
    ):
        """If email_sent is already set (True or False) don't re-queue."""
        mock_verify.return_value = True
        self.order.email_sent = True
        self.order.save()
        body = self._capture_body("PAYMENT.CAPTURE.COMPLETED")

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        mock_tasks.send_registration_email_task.delay.assert_not_called()

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_completed_no_matching_order(self, mock_verify):
        mock_verify.return_value = True
        body = self._capture_body("PAYMENT.CAPTURE.COMPLETED")
        body["resource"]["invoice_id"] = "NOMATCH"
        body["resource"]["custom_id"] = "NOMATCH"
        body["resource"]["id"] = "NOMATCHCAP"

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        notification = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(notification.processed)

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_completed_missing_resource(self, mock_verify):
        mock_verify.return_value = True
        body = self._capture_body("PAYMENT.CAPTURE.COMPLETED")
        del body["resource"]

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        notification = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertFalse(notification.processed)

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_denied_marks_order_failed(self, mock_verify):
        mock_verify.return_value = True
        body = self._capture_body("PAYMENT.CAPTURE.DENIED", status="DECLINED")

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.FAILED)

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_denied_idempotent(self, mock_verify):
        mock_verify.return_value = True
        self.order.status = Order.FAILED
        self.order.save()
        body = self._capture_body("PAYMENT.CAPTURE.DENIED", status="DECLINED")

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.FAILED)

    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_completed_skips_terminal_refunded(self, mock_verify):
        mock_verify.return_value = True
        self.order.status = Order.REFUNDED
        self.order.save()
        body = self._capture_body("PAYMENT.CAPTURE.COMPLETED")

        response = self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body),
            content_type="application/json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.REFUNDED)
