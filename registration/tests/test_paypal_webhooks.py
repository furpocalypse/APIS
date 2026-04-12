import json
import unittest
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
    # to a chargeback or dispute resolution. Critically, the resource is a
    # Capture object, not a Refund object, with resource_type: "capture".
    # Data structure references:
    # - Missing
    # Resource type: 'capture'
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

    # Refund moves to pending
    # Data structure references:
    # - Missing
    # Resource type: 'refund'
    # Do we want to test this: Maybe
    PAYMENT_REFUND_PENDING: str = "PAYMENT.REFUND.PENDING"

    # Refund fails
    # Data structure references:
    # - Missing
    # Resource type: 'refund'
    # Do we want to test this: Maybe
    PAYMENT_REFUND_FAILED: str = "PAYMENT.REFUND.FAILED"

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
        PaypalNotificationEventType.PAYMENT_REFUND_PENDING,
        PaypalNotificationEventType.PAYMENT_REFUND_FAILED,
    ):
        return PaypalResourceType.refund
    elif event_type == PaypalNotificationEventType.PAYMENT_SALE_REFUNDED:
        return PaypalResourceType.sale
    elif event_type == PaypalNotificationEventType.PAYMENT_CAPTURE_REVERSED:
        return PaypalResourceType.capture
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
    elif event_type == PaypalNotificationEventType.PAYMENT_REFUND_PENDING:
        return {
            "id": "5BC28480LY987302A",
            "status": "PENDING",
            "amount": {"currency_code": "USD", "value": "50.00"},
            "invoice_id": "INV-12345",
            "custom_id": "ORDER-67890",
            "seller_payable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": "50.00"},
                "paypal_fee": {"currency_code": "USD", "value": "0.00"},
                "net_amount": {"currency_code": "USD", "value": "50.00"},
                "total_refunded_amount": {"currency_code": "USD", "value": "50.00"},
            },
            "create_time": "2025-01-24T16:44:55Z",
            "update_time": "2025-01-24T16:44:55Z",
            "links": [
                {
                    "href": "https://api.paypal.com/v2/payments/refunds/5BC28480LY987302A",
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
    elif event_type == PaypalNotificationEventType.PAYMENT_REFUND_FAILED:
        return {
            "id": "7GH93021KK456789B",
            "status": "FAILED",
            "amount": {"currency_code": "USD", "value": "99.99"},
            "invoice_id": "INV-12345",
            "custom_id": "ORDER-67890",
            "seller_payable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": "99.99"},
                "paypal_fee": {"currency_code": "USD", "value": "0.00"},
                "net_amount": {"currency_code": "USD", "value": "99.99"},
                "total_refunded_amount": {"currency_code": "USD", "value": "0.00"},
            },
            "create_time": "2025-01-24T16:44:55Z",
            "update_time": "2025-01-24T16:45:00Z",
            "links": [
                {
                    "href": "https://api.paypal.com/v2/payments/refunds/7GH93021KK456789B",
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
        return {
            "id": "3C679366HH908993F",
            "status": "REVERSED",
            "amount": {"currency_code": "USD", "value": "99.99"},
            "final_capture": True,
            "seller_protection": {"status": "NOT_ELIGIBLE"},
            "seller_receivable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": "99.99"},
                "paypal_fee": {"currency_code": "USD", "value": "3.19"},
                "net_amount": {"currency_code": "USD", "value": "96.80"},
            },
            "invoice_id": "INV-12345",
            "custom_id": "ORDER-67890",
            "create_time": "2025-01-20T10:00:00Z",
            "update_time": "2025-01-24T16:45:00Z",
            "links": [
                {
                    "href": "https://api.paypal.com/v2/payments/captures/3C679366HH908993F",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": "https://api.paypal.com/v2/payments/captures/3C679366HH908993F/refund",
                    "rel": "refund",
                    "method": "POST",
                },
                {
                    "href": "https://api.paypal.com/v2/checkout/orders/5O190127TN364715T",
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
        return {}
    elif event_type == PaypalNotificationEventType.CUSTOMER_DISPUTE_RESOLVED:
        return {}

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


@unittest.skip(
    "Blocked on dispatcher rewrite — see "
    "registration/views/paypal_webhooks.py::process_webhook TODO"
)
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
    def test_refund_pending(self, mock_verify):
        """PAYMENT.REFUND.PENDING should set order to REFUND_PENDING and reduce total."""
        mock_verify.return_value = True

        response, body = self._post_webhook(
            PaypalNotificationEventType.PAYMENT_REFUND_PENDING
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.REFUND_PENDING)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_refund_failed(self, mock_verify):
        """PAYMENT.REFUND.FAILED should store notification but order stays COMPLETED."""
        mock_verify.return_value = True

        response, body = self._post_webhook(
            PaypalNotificationEventType.PAYMENT_REFUND_FAILED
        )

        self.assertEqual(response.status_code, 200)

        webhook = PaymentWebhookNotification.objects.get(event_id=body["id"])
        self.assertTrue(webhook.processed)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETED)

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_capture_reversed(self, mock_verify):
        """PAYMENT.CAPTURE.REVERSED (PayPal-initiated reversal/chargeback).
        Resource is a Capture object, not a Refund. Handler must handle this
        format difference and update the order status."""
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

        # PAYMENT_REFUND_PENDING resource has amount $50.00 on a $99.99 order
        response, body = self._post_webhook(
            PaypalNotificationEventType.PAYMENT_REFUND_PENDING
        )

        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        # Order total should be reduced by $50.00
        from decimal import Decimal

        self.assertEqual(self.order.total, Decimal("49.99"))

    @tag("PayPal")
    @patch("registration.views.paypal_webhooks.verify_signature")
    def test_multiple_sequential_refunds(self, mock_verify):
        """Two refund notifications for the same order should both be stored
        and the total reduced cumulatively."""
        mock_verify.return_value = True

        # First refund: $50.00 (PENDING)
        headers1, body1 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_REFUND_PENDING
        )
        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body1),
            content_type="application/json",
            headers=headers1,
        )

        # Second refund: $99.99 COMPLETED (different notification ID)
        headers2, body2 = generate_paypal_notification_example(
            PaypalNotificationEventType.PAYMENT_CAPTURE_REFUNDED
        )
        body2["id"] = "WH-SECOND-NOTIFICATION-ID"
        self.client.post(
            reverse("registration:paypal_webhook"),
            json.dumps(body2),
            content_type="application/json",
            headers=headers2,
        )

        self.order.refresh_from_db()
        refunds = self.order.apiData.get("refunds", [])
        self.assertEqual(len(refunds), 2)

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
    """Documented stubs for webhook scenarios not yet implemented.

    Each stub names expected behavior, PayPal event type, blocking TODO.
    Fill once dispatcher rewrite lands — see
    registration/views/paypal_webhooks.py::process_webhook.
    """

    def test_dispute_updated(self):
        """STUB: CUSTOMER.DISPUTE.UPDATED webhook.

        Expected:
            - HTTP 200
            - PaymentWebhookNotification stored, integration="paypal",
              event_type="CUSTOMER.DISPUTE.UPDATED", processed=True
            - Order.apiData["dispute"] reflects updated dispute_life_cycle_stage
              and any newly submitted evidence

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
            - Empty fixture at
              registration/tests/test_paypal_webhooks.py:303-304

        PayPal reference:
            https://developer.paypal.com/docs/api/customer-disputes/v1/
        """
        self.skipTest("Implementation pending — see docstring")

    def test_dispute_resolved_buyer_favor(self):
        """STUB: CUSTOMER.DISPUTE.RESOLVED with outcome=RESOLVED_BUYER_FAVOUR.

        Expected:
            - HTTP 200
            - PaymentWebhookNotification stored, integration="paypal",
              event_type="CUSTOMER.DISPUTE.RESOLVED", processed=True
            - Order.status transitions to Order.DISPUTE_LOST
            - Order.apiData["dispute"]["outcome"] == "RESOLVED_BUYER_FAVOUR"

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
            - Empty CUSTOMER_DISPUTE_RESOLVED fixture at
              registration/tests/test_paypal_webhooks.py:312-313

        PayPal reference:
            https://developer.paypal.com/docs/api/customer-disputes/v1/
        """
        self.skipTest("Implementation pending — see docstring")

    def test_dispute_resolved_seller_favor(self):
        """STUB: CUSTOMER.DISPUTE.RESOLVED with outcome=RESOLVED_SELLER_FAVOUR.

        Expected:
            - HTTP 200
            - PaymentWebhookNotification stored, integration="paypal",
              event_type="CUSTOMER.DISPUTE.RESOLVED", processed=True
            - Order.status transitions to Order.DISPUTE_WON
            - Any BanList entry added at dispute-created time should be
              reconsidered (behavior TBD — document in dispatcher rewrite)

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
            - Empty CUSTOMER_DISPUTE_RESOLVED fixture at
              registration/tests/test_paypal_webhooks.py:312-313

        PayPal reference:
            https://developer.paypal.com/docs/api/customer-disputes/v1/
        """
        self.skipTest("Implementation pending — see docstring")

    def test_dispute_resolved_accepted(self):
        """STUB: CUSTOMER.DISPUTE.RESOLVED with outcome=ACCEPTED.

        Expected:
            - HTTP 200
            - PaymentWebhookNotification stored, integration="paypal",
              event_type="CUSTOMER.DISPUTE.RESOLVED", processed=True
            - Order.status transitions to Order.DISPUTE_ACCEPTED

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
            - Empty CUSTOMER_DISPUTE_RESOLVED fixture at
              registration/tests/test_paypal_webhooks.py:312-313

        PayPal reference:
            https://developer.paypal.com/docs/api/customer-disputes/v1/
        """
        self.skipTest("Implementation pending — see docstring")

    def test_chargeback_admin_email_sent(self):
        """STUB: CUSTOMER.DISPUTE.CREATED triggers an admin-notification email.

        Expected:
            - HTTP 200
            - An admin email is sent (e.g. via Django's ``mail.outbox``) when
              a dispute/chargeback is opened against an order
            - Email body references the order's reference and the dispute id

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
            - Admin-email hook is not yet wired from the dispute handler

        PayPal reference:
            https://developer.paypal.com/docs/api/customer-disputes/v1/
        """
        self.skipTest("Implementation pending — see docstring")

    def test_unknown_event_type(self):
        """STUB: webhook with an unrecognized event_type.

        Expected:
            - HTTP 200
            - PaymentWebhookNotification stored, integration="paypal",
              processed=False
            - No side effects on Order, Attendee, BanList, or email outbox

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
            - Square-era dispatcher currently silently no-ops unknown types
              rather than explicitly marking processed=False per PayPal spec
        """
        self.skipTest("Implementation pending — see docstring")

    def test_unknown_resource_type(self):
        """STUB: webhook with a known event_type but unexpected resource_type.

        Expected:
            - HTTP 200 (or 400 if handler chooses to reject — decision TBD)
            - PaymentWebhookNotification stored with processed=False
            - Handler logs a warning naming the unexpected resource_type
            - No Order mutations

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
        """
        self.skipTest("Implementation pending — see docstring")

    def test_missing_resource_key(self):
        """STUB: webhook body that lacks the top-level "resource" key.

        Expected:
            - HTTP 400 with a descriptive error, OR
            - HTTP 200 with PaymentWebhookNotification stored and
              processed=False (contract decision TBD in dispatcher rewrite)
            - No Order mutations, no unhandled exceptions

        Blocked by:
            - Dispatcher rewrite at
              registration/views/paypal_webhooks.py::process_webhook
        """
        self.skipTest("Implementation pending — see docstring")
