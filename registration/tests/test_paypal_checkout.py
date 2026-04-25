import json
import uuid
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import Client
from django.test.utils import tag
from django.urls import reverse
from paypalserversdk.exceptions.api_exception import ApiException
from paypalserversdk.http.http_response import HttpResponse
from paypalserversdk.models.order import Order as PayPalOrder

from registration.models import (
    Attendee,
    Badge,
    Cart,
    Discount,
    Order,
    OrderItem,
    PriceLevel,
)
from registration.tests.common import OrdersTestCase
from registration.tests.test_paypal_payments import create_api_response
from registration.views.ordering import do_checkout


def _paypal_order_api_response(order_id: str = "TEST-ORDER", status: str = "CREATED"):
    """Build a minimal PayPal Orders API ApiResponse for mocks."""
    body = {
        "id": order_id,
        "status": status,
        "purchase_units": [
            {
                "reference_id": "registration",
                "amount": {"currency_code": "USD", "value": "0.00"},
            }
        ],
    }
    return create_api_response(body, PayPalOrder)


def _build_api_exception(status_code: int, body: dict) -> ApiException:
    """Build a PayPal ApiException whose .response exposes `text` and `status_code`."""
    response = Mock()
    response.status_code = status_code
    response.text = json.dumps(body)
    return ApiException("Simulated failure", response)


@tag("paypal")
class TestCreatePaypalOrder(OrdersTestCase):
    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_happy_path_single_item(self, mock_create):
        mock_create.return_value = _paypal_order_api_response("TEST-ORDER", "CREATED")
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_create.call_count, 1)
        payload = response.json()
        # common.success wraps the body under "reason"
        self.assertEqual(payload["reason"]["id"], "TEST-ORDER")

    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_donations_added_as_items(self, mock_create):
        mock_create.return_value = _paypal_order_api_response()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({"orgDonation": "5", "charityDonation": "7"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        args, _ = mock_create.call_args
        total_arg, discount_arg, translated_cart = args
        donation_names = [
            item["name"] for item in translated_cart if item.get("donation")
        ]
        self.assertEqual(len(donation_names), 2)
        # Ensure both donation line items are present with the correct totals
        donation_totals = sorted(
            [item["total"] for item in translated_cart if item["donation"]]
        )
        self.assertEqual(donation_totals, [Decimal("5"), Decimal("7")])

    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_negative_donations_clamped(self, mock_create):
        mock_create.return_value = _paypal_order_api_response()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({"orgDonation": "-50", "charityDonation": "-10"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        args, _ = mock_create.call_args
        _total, _discount, translated_cart = args
        # Clamped to 0 -> no donation line items should be present at all
        donations = [item for item in translated_cart if item.get("donation")]
        self.assertEqual(donations, [])

    def test_empty_session_returns_400(self):
        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Session expired", response.json()["reason"])

    def test_invalid_json_returns_400(self):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        response = self.client.post(
            reverse("registration:paypalcreate"),
            "not valid json{",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_api_exception_propagates(self, mock_create):
        mock_create.side_effect = _build_api_exception(
            422, {"name": "UNPROCESSABLE_ENTITY", "details": []}
        )
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)
        # common.abort wraps the reason
        self.assertEqual(response.json()["reason"]["name"], "UNPROCESSABLE_ENTITY")

    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_discount_applied(self, mock_create):
        mock_create.return_value = _paypal_order_api_response()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        # Apply a discount via session
        session = self.client.session
        session["discount"] = self.discount.codeName  # "FiveOff" -> $5 off
        session.save()

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        args, _ = mock_create.call_args
        _total, total_discount, _cart = args
        # FiveOff is an amountOff=5.00 discount; total_discount should be 5.
        self.assertEqual(Decimal(total_discount), Decimal("5.00"))

    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_order_items_path(self, mock_create):
        """Ensure the endpoint still calls PayPal when session has order_items."""
        mock_create.return_value = _paypal_order_api_response()
        # Create an Attendee/Badge/OrderItem directly and wire to session.
        attendee = Attendee.objects.create(
            firstName="Pre",
            lastName="Existing",
            address1="x",
            city="x",
            state="PA",
            country="US",
            postalCode="12345",
            phone="0",
            email="pre@example.org",
            birthdate="1990-01-01",
        )
        badge = Badge.objects.create(attendee=attendee, event=self.event)
        order_item = OrderItem.objects.create(
            badge=badge, priceLevel=self.price_45, enteredBy="test"
        )

        session = self.client.session
        session["order_items"] = [order_item.id]
        session.save()

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_create.call_count, 1)

    @patch("registration.views.ordering.create_unpaid_paypal_order")
    def test_zero_total_short_circuits(self, mock_create):
        """Cart totalling 0 with no donations should skip the PayPal API
        call and return 400 directing the caller to the zero-checkout flow.

        Production behavior (ordering.py): when the computed total is zero,
        ``create_paypal_order`` rejects the request with HTTP 400 rather than
        invoking PayPal. Callers are expected to route zero-total carts to
        ``checkout`` where ``doZeroCheckout`` handles the no-payment path.
        """
        self.add_to_cart(self.attendee_form_1, self.price_free, [])

        response = self.client.post(
            reverse("registration:paypalcreate"),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("zero", response.json()["reason"].lower())
        mock_create.assert_not_called()


@tag("paypal")
class TestDoPaypalCheckout(OrdersTestCase):
    """Unit tests for do_checkout with PayPal as the processor."""

    def _make_cart(self):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        cart_ids = self.client.session.get("cart_items", [])
        return list(Cart.objects.filter(id__in=cart_ids))

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_capture_success_saves_order_and_items(self, mock_capture):
        mock_capture.return_value = (True, {"id": "TEST", "status": "COMPLETED"})
        cart_items = self._make_cart()

        status, msg, order = do_checkout(
            "paypal",
            {"source_id": "TEST-PAYPAL-ORDER"},
            Decimal("45"),
            None,
            cart_items,
            [],
            Decimal("0"),
            Decimal("0"),
        )

        self.assertTrue(status)
        self.assertDictEqual(msg, {"errors": []})
        self.assertIsNotNone(order.pk)
        self.assertGreaterEqual(OrderItem.objects.filter(order=order).count(), 1)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_capture_failure_does_not_save_items(self, mock_capture):
        mock_capture.return_value = (
            False,
            {"errors": [{"code": "CARD_DECLINED"}]},
        )
        cart_items = self._make_cart()

        status, response, order = do_checkout(
            "paypal",
            {"source_id": "TEST-PAYPAL-ORDER"},
            Decimal("45"),
            None,
            cart_items,
            [],
            Decimal("0"),
            Decimal("0"),
        )

        self.assertFalse(status)
        self.assertIn("errors", response)
        # Under the pending-order approach the Order + cart items are
        # persisted before payment is attempted so capacity counters and
        # DB rows stay consistent. A failed capture leaves them in place
        # with Order.status == FAILED (do_checkout normalizes PENDING →
        # FAILED when the payment path returned False without saving).
        self.assertIsNotNone(order.pk)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.FAILED)
        self.assertGreaterEqual(OrderItem.objects.filter(order=order).count(), 1)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_order_items_branch(self, mock_capture):
        """Pre-existing OrderItem objects (upgrade flow) get reattached."""
        mock_capture.return_value = (True, {"id": "T", "status": "COMPLETED"})

        attendee = Attendee.objects.create(
            firstName="Up",
            lastName="Grade",
            address1="x",
            city="x",
            state="PA",
            country="US",
            postalCode="12345",
            phone="0",
            email="up@example.org",
            birthdate="1990-01-01",
        )
        badge = Badge.objects.create(attendee=attendee, event=self.event)
        order_item = OrderItem.objects.create(
            badge=badge, priceLevel=self.price_45, enteredBy="test"
        )

        status, _msg, order = do_checkout(
            "paypal",
            {"source_id": "TEST-PAYPAL-ORDER"},
            Decimal("45"),
            None,
            [],
            [order_item],
            Decimal("0"),
            Decimal("0"),
        )

        self.assertTrue(status)
        order_item.refresh_from_db()
        self.assertEqual(order_item.order_id, order.id)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_no_discount(self, mock_capture):
        mock_capture.return_value = (True, {"id": "T", "status": "COMPLETED"})
        cart_items = self._make_cart()

        status, _msg, _order = do_checkout(
            "paypal",
            {"source_id": "TEST-PAYPAL-ORDER"},
            Decimal("45"),
            None,
            cart_items,
            [],
            Decimal("0"),
            Decimal("0"),
        )
        self.assertTrue(status)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_discount_used_incremented(self, mock_capture):
        mock_capture.return_value = (True, {"id": "T", "status": "COMPLETED"})
        cart_items = self._make_cart()
        original_used = self.discount.used

        status, _msg, _order = do_checkout(
            "paypal",
            {"source_id": "TEST-PAYPAL-ORDER"},
            Decimal("40"),
            self.discount,
            cart_items,
            [],
            Decimal("0"),
            Decimal("0"),
        )

        self.assertTrue(status)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.used, original_used + 1)


@tag("paypal")
class TestCheckoutEndpointPayPal(OrdersTestCase):
    """Integration tests for the checkout POST endpoint on the PayPal path."""

    def _idempotency_headers(self):
        return {"idempotency-key": str(uuid.uuid4())}

    def test_missing_orderID_returns_400(self):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        response = self.client.post(
            reverse("registration:checkout"),
            json.dumps({"processor": "paypal", "onsite": False, "billingData": {}}),
            content_type="application/json",
            headers=self._idempotency_headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing PayPal order ID", response.json()["reason"])

    def test_invalid_json_returns_400(self):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        response = self.client.post(
            reverse("registration:checkout"),
            "{not-json",
            content_type="application/json",
            headers=self._idempotency_headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_session_expired_returns_400(self):
        fresh = Client()
        response = fresh.post(
            reverse("registration:checkout"),
            json.dumps({"onsite": False, "orderID": "TEST-PAYPAL-ORDER"}),
            content_type="application/json",
            headers={"idempotency-key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 400)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_capture_failure_returns_400(self, mock_capture):
        mock_capture.return_value = (
            False,
            {"errors": [{"code": "CARD_DECLINED"}]},
        )
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        response = self.checkout()
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("errors", body["reason"])

    def test_missing_idempotency_key_returns_400(self):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        response = self.client.post(
            reverse("registration:checkout"),
            json.dumps(
                {
                    "processor": "paypal",
                    "onsite": False,
                    "billingData": {"source_id": "TEST-PAYPAL-ORDER"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_onsite_true_sets_unpaid_status_and_skips_paypal(self, mock_capture):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        post_data = {
            "billingData": {},
            "charityDonation": "",
            "onsite": True,
            "orgDonation": "",
            "orderID": "TEST-PAYPAL-ORDER",
        }
        response = self.client.post(
            reverse("registration:checkout"),
            json.dumps(post_data),
            content_type="application/json",
            headers=self._idempotency_headers(),
        )
        self.assertEqual(response.status_code, 200)
        mock_capture.assert_not_called()

        order = Order.objects.latest("id")
        self.assertEqual(order.billingType, Order.UNPAID)
        self.assertEqual(order.status, "Onsite Pending")

    @patch("registration.tasks.send_registration_email_task.delay")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_happy_path_success_sends_registration_email(
        self, mock_capture, mock_email
    ):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        response = self.checkout("cnon:card-nonce-ok", "1", "10")
        self.assertEqual(response.status_code, 200)

        mock_email.assert_called_once()
        order = Order.objects.latest("id")
        args, _kwargs = mock_email.call_args
        self.assertEqual(args[0], order.id)
        self.assertEqual(args[1], order.billingEmail)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_discount_used_increments_once(self, mock_capture):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        self.add_to_cart(self.attendee_form_2, self.price_45, [])

        # Apply discount via the session.
        session = self.client.session
        session["discount"] = self.discount.codeName
        session.save()

        original_used = Discount.objects.get(pk=self.discount.pk).used

        response = self.checkout("cnon:card-nonce-ok")
        self.assertEqual(response.status_code, 200)

        updated = Discount.objects.get(pk=self.discount.pk)
        self.assertEqual(updated.used, original_used + 1)
