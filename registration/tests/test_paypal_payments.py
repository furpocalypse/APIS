import json
from decimal import Decimal
from typing import Any, Protocol, cast
from unittest.mock import Mock, patch

from django.test import override_settings, tag
from paypalserversdk.exceptions.api_exception import ApiException
from paypalserversdk.http.api_response import ApiResponse
from paypalserversdk.http.http_response import HttpResponse
from paypalserversdk.models.capture_status import CaptureStatus
from paypalserversdk.models.captured_payment import CapturedPayment
from paypalserversdk.models.order import Order as PayPalOrder
from paypalserversdk.models.refund import Refund
from paypalserversdk.models.refund_status import RefundStatus

from registration.models import Event, Order
from registration.paypal_payments import (
    capture_paypal_payment,
    create_unpaid_paypal_order,
    get_available_refund_amount,
    refresh_payment,
    refund_card_payment,
    refund_payment,
    update_order_payment_data,
    update_order_refund_data,
)
from registration.tests.common import (
    DEFAULT_EVENT_ARGS,
    OrdersTestCase,
    make_refund_dict,
    paypal_apidata_fully_refunded,
    paypal_apidata_multi_full,
    paypal_apidata_multi_partial,
    paypal_apidata_no_refunds,
    paypal_apidata_one_partial_refund,
)


def generate_refund_mock(id: str, status: RefundStatus, amount: float, reason: str) -> str:
    return """{{
        "id": "{id}",
        "amount": {{
            "value": "{amount:.2f}",
            "currency_code": "USD"
        }},
        "status": "{status}",
        "note_to_payer": "{reason}",
        "create_time": "2018-09-11T23:24:19Z",
        "update_time": "2018-09-11T23:24:19Z"
    }}""".format(**locals())


class _PayPalModelClass(Protocol):
    """The shared shape of every PayPal SDK model class used as a response body.

    Each generated SDK model exposes a ``from_dictionary`` classmethod that
    deserializes a parsed-JSON ``dict`` into a model instance.
    """

    def from_dictionary(self, dictionary: Any) -> Any: ...


def create_api_response(
    body: str | dict, body_type: _PayPalModelClass, code: int | None = 200
) -> ApiResponse:
    """Creates an ApiResponse object to be used in a mock.

    :param body: The response body. Should be valid JSON.
    :param body_type: The PayPal SDK model class to instantiate from the body.
    :param code: HTTP response code.
    :return: A constructed ApiResponse
    """
    if isinstance(body, dict):
        body = json.dumps(body)

    return ApiResponse(
        HttpResponse(
            status_code=code,
            reason_phrase="Doesn't matter much",
            headers=[],
            text=body,
            request=None,
        ),
        body_type.from_dictionary(json.loads(body)),
    )


def _make_api_exception(status_code: int, body: dict | str) -> ApiException:
    """Build an ApiException whose .response exposes a JSON text body."""
    if isinstance(body, dict):
        body = json.dumps(body)
    response = Mock(spec=HttpResponse)
    response.status_code = status_code
    response.text = body
    return ApiException("Simulated PayPal API failure", response)


def _make_paypal_order_response(
    *,
    order_id: str = "test_order",
    capture_status: str = CaptureStatus.COMPLETED,
    capture_amount: str = "95.00",
    refunds: list | None = None,
    include_card: bool = True,
) -> ApiResponse:
    """Build a PayPal GET /orders response wrapped as an ApiResponse."""
    body: dict[str, Any] = {
        "id": order_id,
        "purchase_units": [
            {
                "reference_id": "registration",
                "amount": {
                    "currency_code": "USD",
                    "value": capture_amount,
                },
                "payments": {
                    "captures": [
                        {
                            "status": capture_status,
                            "id": "test_order_payment",
                            "amount": {
                                "currency_code": "USD",
                                "value": capture_amount,
                            },
                        }
                    ],
                },
            }
        ],
    }
    if include_card:
        body["payment_source"] = {"card": {"last_digits": "1234"}}
    if refunds is not None:
        body["purchase_units"][0]["payments"]["refunds"] = refunds
    return create_api_response(body, PayPalOrder)


# ---------------------------------------------------------------------------
# A.1 - create_unpaid_paypal_order
# ---------------------------------------------------------------------------


@tag("paypal")
class TestCreateUnpaidPaypalOrder(OrdersTestCase):
    """Matrix section A.1 - exercises create_unpaid_paypal_order()."""

    @staticmethod
    def _single_item(name="Test Reg", total="45.00"):
        return {"name": name, "total": total, "donation": False}

    def _mock_created_response(self):
        body = {
            "id": "ORD-NEW",
            "status": "CREATED",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {"currency_code": "USD", "value": "45.00"},
                }
            ],
        }
        return create_api_response(body, PayPalOrder, 201)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_1_single_item_no_discount(self, mock_create):
        mock_create.return_value = self._mock_created_response()

        response = create_unpaid_paypal_order(
            total="45.00", discount="0", cart_items=[self._single_item()]
        )

        self.assertIsNotNone(response)
        mock_create.assert_called_once()
        call_args = mock_create.call_args[0][0]
        order_request = call_args["body"]
        self.assertEqual(1, len(order_request.purchase_units))
        pu = order_request.purchase_units[0]
        self.assertEqual("registration", pu.reference_id)
        self.assertEqual("USD", pu.amount.currency_code)
        self.assertEqual("45.00", pu.amount.value)
        self.assertEqual(1, len(pu.items))

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_2_multi_item_cart(self, mock_create):
        mock_create.return_value = self._mock_created_response()
        items = [
            self._single_item("Reg A", "45.00"),
            self._single_item("Reg B", "90.00"),
            self._single_item("Reg C", "150.00"),
        ]

        create_unpaid_paypal_order(total="285.00", discount="0", cart_items=items)

        call_args = mock_create.call_args[0][0]
        pu = call_args["body"].purchase_units[0]
        self.assertEqual(3, len(pu.items))

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_3_discount_passed_through(self, mock_create):
        mock_create.return_value = self._mock_created_response()

        create_unpaid_paypal_order(
            total="45", discount="5", cart_items=[self._single_item(total="45")]
        )

        call_args = mock_create.call_args[0][0]
        breakdown = call_args["body"].purchase_units[0].amount.breakdown
        self.assertEqual("5", breakdown.discount.value)
        self.assertEqual("45", breakdown.item_total.value)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_4_donation_items_split_into_own_purchase_unit(self, mock_create):
        """Donations go into a separate purchase_unit with ItemCategory.DONATION
        so PayPal receipts and merchant reporting attribute them correctly."""
        mock_create.return_value = self._mock_created_response()
        items = [
            self._single_item("Attendee", "45.00"),
            {"name": "Org Donation", "total": "5.00", "donation": True},
            {"name": "Charity Donation", "total": "3.00", "donation": True},
        ]

        create_unpaid_paypal_order(total="53.00", discount="0", cart_items=items)

        call_args = mock_create.call_args[0][0]
        request = call_args["body"]
        self.assertEqual(2, len(request.purchase_units))

        reg_pu = request.purchase_units[0]
        don_pu = request.purchase_units[1]
        self.assertEqual("registration", reg_pu.reference_id)
        self.assertEqual("donation", don_pu.reference_id)
        self.assertEqual(1, len(reg_pu.items))
        self.assertEqual(2, len(don_pu.items))
        self.assertEqual("45.00", reg_pu.amount.value)
        self.assertEqual("8.00", don_pu.amount.value)
        self.assertEqual("DIGITAL_GOODS", reg_pu.items[0].category)
        for item in don_pu.items:
            self.assertEqual("DONATION", item.category)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_5_empty_cart(self, mock_create):
        mock_create.return_value = self._mock_created_response()

        create_unpaid_paypal_order(total="0", discount="0", cart_items=[])

        call_args = mock_create.call_args[0][0]
        pu = call_args["body"].purchase_units[0]
        self.assertEqual(0, len(pu.items))

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_6_api_exception_propagates(self, mock_create):
        mock_create.side_effect = _make_api_exception(500, {"name": "INTERNAL", "message": "boom"})
        with self.assertRaises(ApiException):
            create_unpaid_paypal_order(total="45", discount="0", cart_items=[self._single_item()])

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A1_7_item_category_digital_goods(self, mock_create):
        mock_create.return_value = self._mock_created_response()

        create_unpaid_paypal_order(total="45", discount="0", cart_items=[self._single_item()])

        call_args = mock_create.call_args[0][0]
        item = call_args["body"].purchase_units[0].items[0]
        self.assertEqual("DIGITAL_GOODS", item.category)


# ---------------------------------------------------------------------------
# A.1b - donation/registration split in create_unpaid_paypal_order
# ---------------------------------------------------------------------------


@tag("paypal")
class TestCreateUnpaidPaypalOrderDonationSplit(OrdersTestCase):
    """Exercises the donation/registration purchase_unit split introduced to
    replace the DIGITAL_GOODS-everything workaround."""

    @staticmethod
    def _reg(name="Reg", total="45.00"):
        return {"name": name, "total": total, "donation": False}

    @staticmethod
    def _don(name="Donation", total="5.00"):
        return {"name": name, "total": total, "donation": True}

    def _mock_created_response(self):
        body = {
            "id": "ORD-NEW",
            "status": "CREATED",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {"currency_code": "USD", "value": "0.00"},
                }
            ],
        }
        return create_api_response(body, PayPalOrder, 201)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_donations_only_cart_emits_single_donation_unit(self, mock_create):
        mock_create.return_value = self._mock_created_response()

        create_unpaid_paypal_order(
            total="12.00",
            discount="0",
            cart_items=[self._don("A", "7.00"), self._don("B", "5.00")],
            apis_reference="REF-DON-ONLY",
        )

        request = mock_create.call_args[0][0]["body"]
        self.assertEqual(1, len(request.purchase_units))
        pu = request.purchase_units[0]
        self.assertEqual("donation", pu.reference_id)
        self.assertEqual("12.00", pu.amount.value)
        self.assertEqual("12.00", pu.amount.breakdown.item_total.value)
        self.assertFalse(hasattr(pu.amount.breakdown, "discount"))
        self.assertEqual("REF-DON-ONLY-don", pu.invoice_id)
        self.assertEqual("REF-DON-ONLY", pu.custom_id)
        self.assertEqual(2, len(pu.items))
        for item in pu.items:
            self.assertEqual("DONATION", item.category)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_registrations_only_cart_emits_single_registration_unit(self, mock_create):
        mock_create.return_value = self._mock_created_response()

        create_unpaid_paypal_order(
            total="90.00",
            discount="0",
            cart_items=[self._reg("A", "45.00"), self._reg("B", "45.00")],
            apis_reference="REF-REG-ONLY",
        )

        request = mock_create.call_args[0][0]["body"]
        self.assertEqual(1, len(request.purchase_units))
        pu = request.purchase_units[0]
        self.assertEqual("registration", pu.reference_id)
        self.assertEqual("REF-REG-ONLY", pu.invoice_id)
        self.assertEqual("REF-REG-ONLY", pu.custom_id)
        self.assertEqual(2, len(pu.items))
        for item in pu.items:
            self.assertEqual("DIGITAL_GOODS", item.category)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_mixed_cart_with_discount_applies_only_to_registration_unit(self, mock_create):
        mock_create.return_value = self._mock_created_response()
        items = [
            self._reg("Attendee", "50.00"),
            self._don("Org Donation", "10.00"),
        ]

        create_unpaid_paypal_order(
            total="60.00",
            discount="5",
            cart_items=items,
            apis_reference="REF-MIXED",
        )

        request = mock_create.call_args[0][0]["body"]
        self.assertEqual(2, len(request.purchase_units))

        reg_pu, don_pu = request.purchase_units
        self.assertEqual("registration", reg_pu.reference_id)
        self.assertEqual("donation", don_pu.reference_id)

        # Registration unit bears the discount: 50.00 - 5 = 45.00.
        self.assertEqual("45.00", reg_pu.amount.value)
        self.assertEqual("50.00", reg_pu.amount.breakdown.item_total.value)
        self.assertEqual("5", reg_pu.amount.breakdown.discount.value)
        self.assertEqual("REF-MIXED", reg_pu.invoice_id)
        self.assertEqual("REF-MIXED", reg_pu.custom_id)

        # Donation unit carries its full value and suffixed invoice_id.
        self.assertEqual("10.00", don_pu.amount.value)
        self.assertEqual("10.00", don_pu.amount.breakdown.item_total.value)
        self.assertFalse(hasattr(don_pu.amount.breakdown, "discount"))
        self.assertEqual("REF-MIXED-don", don_pu.invoice_id)
        self.assertEqual("REF-MIXED", don_pu.custom_id)

        # Both units together reconcile to the post-discount charge.
        total_charged = Decimal(reg_pu.amount.value) + Decimal(don_pu.amount.value)
        self.assertEqual(Decimal("55.00"), total_charged)


# ---------------------------------------------------------------------------
# A.2 - capture_paypal_payment
# ---------------------------------------------------------------------------


@tag("paypal")
class TestCapturePaypalPayment(OrdersTestCase):
    """Matrix section A.2 - exercises capture_paypal_payment()."""

    def setUp(self):
        super().setUp()
        self.apis_order = Order(
            total=95,
            status=Order.PENDING,
            reference="CAP-REF",
        )
        self.apis_order.save()

    @staticmethod
    def _success_body(card=True):
        body = {
            "id": "ABCDEF1234",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {"currency_code": "USD", "value": "95.00"},
                    "payments": {
                        "captures": [
                            {
                                "id": "cap1",
                                "status": CaptureStatus.COMPLETED,
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "95.00",
                                },
                            }
                        ]
                    },
                }
            ],
        }
        if card:
            body["payment_source"] = {"card": {"last_digits": "4242"}}
        return body

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A2_1_happy_path_completed_card(self, mock_capture):
        mock_capture.return_value = create_api_response(self._success_body(card=True), PayPalOrder)

        ok, _body = capture_paypal_payment("ABCDEF1234", self.apis_order)

        self.assertTrue(ok)
        self.apis_order.refresh_from_db()
        self.assertEqual(Order.COMPLETED, self.apis_order.status)
        self.assertEqual("4242", self.apis_order.lastFour)
        # apiData is the raw text
        self.assertEqual("ABCDEF1234", self.apis_order.apiData.get("id"))

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A2_2_paypal_balance_no_last_four(self, mock_capture):
        body = self._success_body(card=False)
        body["payment_source"] = {"paypal": {"email_address": "x@example.com"}}
        mock_capture.return_value = create_api_response(body, PayPalOrder)

        ok, _ = capture_paypal_payment("ABCDEF1234", self.apis_order)

        self.assertTrue(ok)
        self.apis_order.refresh_from_db()
        self.assertEqual("", self.apis_order.lastFour)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A2_3_api_exception_uses_ex_response(self, mock_capture):
        """capture_order raises ApiException; production sets
        api_response = ex.response. We point ex.response at an
        ApiResponse whose is_error() is True and whose text carries an
        errors array.
        TODO: SDK error-body shape is unclear; mock ApiResponse directly."""
        err_body = {
            "id": "ABCDEF1234",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {"currency_code": "USD", "value": "95.00"},
                    "payments": {
                        "captures": [
                            {
                                "id": "cap1",
                                "status": CaptureStatus.DECLINED,
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "95.00",
                                },
                            }
                        ]
                    },
                }
            ],
            "errors": [{"issue": "whatever"}],
        }
        real_response = create_api_response(err_body, PayPalOrder, 500)
        exc = ApiException.__new__(ApiException)
        exc.reason = "nope"
        exc.response = real_response
        exc.response_code = 500
        mock_capture.side_effect = exc

        ok, body = capture_paypal_payment("ABCDEF1234", self.apis_order)

        self.assertFalse(ok)
        self.assertIn("errors", body)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A2_4_is_error_marks_failed(self, mock_capture):
        err_body = {
            "id": "ABCDEF1234",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {"currency_code": "USD", "value": "95.00"},
                    "payments": {
                        "captures": [
                            {
                                "id": "cap1",
                                "status": CaptureStatus.DECLINED,
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "95.00",
                                },
                            }
                        ]
                    },
                }
            ],
            "errors": [{"issue": "INSTRUMENT_DECLINED"}],
        }
        mock_capture.return_value = create_api_response(err_body, PayPalOrder, 422)

        ok, body = capture_paypal_payment("ABCDEF1234", self.apis_order)

        self.assertFalse(ok)
        self.assertIn("errors", body)
        self.apis_order.refresh_from_db()
        self.assertEqual(Order.FAILED, self.apis_order.status)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A2_5_notes_set_on_success(self, mock_capture):
        mock_capture.return_value = create_api_response(self._success_body(), PayPalOrder)

        capture_paypal_payment("ABCDEF1234", self.apis_order)

        self.apis_order.refresh_from_db()
        self.assertEqual("PayPal: #ABCD", self.apis_order.notes)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A2_6_missing_payment_source_key(self, mock_capture):
        body = self._success_body(card=False)
        # Ensure payment_source not present at all
        body.pop("payment_source", None)
        mock_capture.return_value = create_api_response(body, PayPalOrder)

        ok, _ = capture_paypal_payment("ABCDEF1234", self.apis_order)

        self.assertTrue(ok)
        self.apis_order.refresh_from_db()
        self.assertEqual("", self.apis_order.lastFour)


# ---------------------------------------------------------------------------
# A.3 - refresh_payment (extends existing TestPaymentRefresh)
# ---------------------------------------------------------------------------


@tag("paypal")
class TestPaymentRefresh(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.event = Event(**DEFAULT_EVENT_ARGS)
        self.event.save()

        self.captured_payment_response = create_api_response(
            {
                "id": "test_order",
                "payment_source": {"card": {"last_digits": "1234"}},
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "status": CaptureStatus.COMPLETED,
                                    "id": "test_order_payment",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": "95.00",
                                    },
                                }
                            ]
                        }
                    }
                ],
            },
            PayPalOrder,
        )

        self.declined_payment_response = create_api_response(
            {
                "id": "test_order",
                "payment_source": {"card": {"last_digits": "1234"}},
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "status": CaptureStatus.DECLINED,
                                    "id": "test_order_payment",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": "95.00",
                                    },
                                }
                            ]
                        }
                    }
                ],
            },
            PayPalOrder,
        )

        self.refund_complete_response = create_api_response(
            {
                "id": "test_order",
                "payment_source": {"card": {"last_digits": "1234"}},
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "status": CaptureStatus.COMPLETED,
                                    "id": "test_order_payment",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": "95.00",
                                    },
                                }
                            ],
                            "refunds": [
                                {
                                    "status": RefundStatus.COMPLETED,
                                    "id": "test_order_refund",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": f"{self.price_45.basePrice:.2f}",
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            PayPalOrder,
        )

        self.refund_pending_response = create_api_response(
            {
                "id": "test_order",
                "payment_source": {"card": {"last_digits": "1234"}},
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "status": CaptureStatus.COMPLETED,
                                    "id": "test_order_payment",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": "95.00",
                                    },
                                }
                            ],
                            "refunds": [
                                {
                                    "status": RefundStatus.PENDING,
                                    "id": "test_order_refund",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": f"{self.price_45.basePrice:.2f}",
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            PayPalOrder,
        )

        self.test_order = Order()
        self.test_order.total = self.price_45.basePrice + 50
        self.test_order.orgDonation = 25
        self.test_order.charityDonation = 25
        self.test_order.status = Order.PENDING
        self.test_order.apiData = {
            "id": "test_order",
            "payment_source": {"card": {"last_digits": "1234"}},
            "purchase_units": {
                "payments": [
                    {
                        "status": CaptureStatus.PENDING,
                        "id": "test_order_payment",
                        "amount": {"currency_code": "USD", "value": "95.00"},
                    }
                ]
            },
        }
        self.test_order.save()

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_captured_payment_refresh(self, mock_get_order: Mock):
        mock_get_order.return_value = self.captured_payment_response
        result, message = refresh_payment(self.test_order)

        mock_get_order.assert_called_once()
        self.assertTrue(result, message)
        self.assertIsNone(message)

        self.test_order.refresh_from_db()
        self.assertEqual(self.price_45.basePrice + 50, self.test_order.total)
        self.assertEqual(Order.COMPLETED, self.test_order.status)
        self.assertEqual(25, self.test_order.orgDonation)
        self.assertEqual(25, self.test_order.charityDonation)

    # --- A3.2 DECLINED ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_2_declined_capture(self, mock_get):
        mock_get.return_value = self.declined_payment_response
        _ok, _ = refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.FAILED, self.test_order.status)

    # --- A3.3 PARTIALLY_REFUNDED ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_3_partially_refunded_capture(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(
            capture_status=CaptureStatus.PARTIALLY_REFUNDED
        )
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.REFUNDED, self.test_order.status)

    # --- A3.4 PENDING capture ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_4_pending_capture(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(
            capture_status=CaptureStatus.PENDING, capture_amount="95.00"
        )
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.PENDING, self.test_order.status)
        self.assertEqual(95, self.test_order.total)

    # --- A3.5 REFUNDED capture ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_5_refunded_capture(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(capture_status=CaptureStatus.REFUNDED)
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.REFUNDED, self.test_order.status)

    # --- A3.6 FAILED capture ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_6_failed_capture(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(capture_status=CaptureStatus.FAILED)
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.FAILED, self.test_order.status)

    # --- A3.7 Completed refunds -> REFUNDED ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_7_completed_refunds_processed(self, mock_get):
        mock_get.return_value = self.refund_complete_response
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.REFUNDED, self.test_order.status)

    # --- A3.8 Pending refunds -> REFUND_PENDING ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_8_pending_refunds_processed(self, mock_get):
        mock_get.return_value = self.refund_pending_response
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.REFUND_PENDING, self.test_order.status)

    # --- A3.9 Refund drives donations to reset ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_9_refund_resets_donations(self, mock_get):
        # capture=95, refund reduces total; orgDonation+charityDonation=50.
        # Capture completed; apis order.total stays at 95 from payment; we
        # want update_order_refund_data to fire the reset branch. That branch
        # checks order.orgDonation + order.charityDonation > order.total. So
        # force order.total small by making capture 10.
        mock_get.return_value = _make_paypal_order_response(
            capture_status=CaptureStatus.COMPLETED,
            capture_amount="10.00",
            refunds=[
                {
                    "status": RefundStatus.COMPLETED,
                    "id": "r1",
                    "amount": {"currency_code": "USD", "value": "5.00"},
                }
            ],
        )
        self.test_order.orgDonation = 25
        self.test_order.charityDonation = 25
        self.test_order.notes = ""
        self.test_order.save()

        _ok, message = refresh_payment(self.test_order)

        self.test_order.refresh_from_db()
        self.assertEqual(0, self.test_order.orgDonation)
        self.assertEqual(self.test_order.total, self.test_order.charityDonation)
        self.assertIn("reset", message or "")
        self.assertIn("reset", self.test_order.notes)

    # --- A3.10 store_api_data override ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_10_store_api_data_override(self, mock_get):
        mock_get.return_value = self.captured_payment_response
        override = {
            "id": "override_order_id",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "cap",
                                "amount": {"currency_code": "USD", "value": "95.00"},
                            }
                        ]
                    }
                }
            ],
        }
        self.test_order.apiData = None
        self.test_order.save()
        ok, _ = refresh_payment(self.test_order, store_api_data=override)
        self.assertTrue(ok)
        call_kwargs = mock_get.call_args[0][0]
        self.assertEqual("override_order_id", call_kwargs["id"])

    # --- A3.11 missing apiData ---
    def test_A3_11_missing_api_data(self):
        self.test_order.apiData = None
        self.test_order.save()
        ok, message = refresh_payment(self.test_order)
        self.assertFalse(ok)
        self.assertIn("No order data yet", message)

    # --- A3.12 ApiException from get_order ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_12_api_exception_from_get_order(self, mock_get):
        mock_get.side_effect = _make_api_exception(500, {"name": "INTERNAL", "message": "blew up"})
        ok, message = refresh_payment(self.test_order)
        self.assertFalse(ok)
        self.assertEqual("blew up", message)

    # --- A3.13 from_dictionary failure -> MISSING_PAYMENT_ID ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_13_from_dictionary_failure(self, mock_get):
        # Make api_data a list so PayPalOrder.from_dictionary trips AttributeError.
        self.test_order.apiData = [{"this": "is wrong"}]
        self.test_order.save()
        mock_get.return_value = self.captured_payment_response
        ok, message = refresh_payment(self.test_order)
        self.assertFalse(ok)
        self.assertEqual("MISSING_PAYMENT_ID", message)

    # --- A3.14 response is_error ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_14_response_is_error(self, mock_get):
        mock_get.return_value = ApiResponse(
            HttpResponse(
                status_code=500,
                reason_phrase="err",
                headers=[],
                text=json.dumps({"message": "gateway error"}),
                request=None,
            ),
            None,
        )
        ok, message = refresh_payment(self.test_order)
        self.assertFalse(ok)
        self.assertEqual("gateway error", message)

    # --- A3.15 malformed purchase_units ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_15_malformed_purchase_units(self, mock_get):
        body = {
            "id": "test_order",
            "purchase_units": [{"payments": {"captures": []}}],
        }
        mock_get.return_value = create_api_response(body, PayPalOrder)
        ok, message = refresh_payment(self.test_order)
        self.assertFalse(ok)
        self.assertEqual("Malformed payment data!", message)

    # --- A3.16 payment source without card ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_16_payment_source_without_card(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(include_card=False)
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual("", self.test_order.lastFour)

    # --- A3.17 CANCELLED refund present ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_17_cancelled_refund(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(
            refunds=[
                {
                    "status": RefundStatus.CANCELLED,
                    "id": "r",
                    "amount": {"currency_code": "USD", "value": "5.00"},
                }
            ]
        )
        # Must not crash; CANCELLED does not drive REFUND_PENDING or REFUNDED.
        try:
            refresh_payment(self.test_order)
        except Exception as exc:
            self.fail(f"refresh_payment raised unexpectedly: {exc!r}")
        self.test_order.refresh_from_db()
        self.assertEqual(Order.COMPLETED, self.test_order.status)

    # --- A3.18 FAILED refund present ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_18_failed_refund(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(
            refunds=[
                {
                    "status": RefundStatus.FAILED,
                    "id": "r",
                    "amount": {"currency_code": "USD", "value": "5.00"},
                }
            ]
        )
        try:
            refresh_payment(self.test_order)
        except Exception as exc:
            self.fail(f"refresh_payment raised unexpectedly: {exc!r}")
        self.test_order.refresh_from_db()
        self.assertEqual(Order.COMPLETED, self.test_order.status)

    # --- A3.19 mix: PENDING dominates ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_19_mix_pending_dominates(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(
            refunds=[
                make_refund_dict(id="c", status=RefundStatus.COMPLETED, amount=5),
                make_refund_dict(id="p", status=RefundStatus.PENDING, amount=5),
                make_refund_dict(id="f", status=RefundStatus.FAILED, amount=5),
            ]
        )
        refresh_payment(self.test_order)
        self.test_order.refresh_from_db()
        self.assertEqual(Order.REFUND_PENDING, self.test_order.status)

    # --- A3.20 absent refunds key ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_20_absent_refunds_key(self, mock_get):
        mock_get.return_value = _make_paypal_order_response(refunds=None)
        try:
            refresh_payment(self.test_order)
        except Exception as exc:
            self.fail(f"refresh_payment raised unexpectedly: {exc!r}")
        self.test_order.refresh_from_db()
        self.assertEqual(Order.COMPLETED, self.test_order.status)

    # --- A3.21 contract: never raises ---
    @patch("paypalserversdk.controllers.orders_controller.OrdersController.get_order")
    def test_A3_21_never_raises_contract(self, mock_get):
        # Whatever pathological input we throw at it, it must not raise.
        inputs = [
            (_make_api_exception(500, "not json"), None),
            (_make_api_exception(500, {"foo": "bar"}), None),
            (ApiException("no response", Mock(status_code=0, text="")), None),
            (None, "broken"),  # force is_error path
        ]
        for side, _ in inputs:
            if side is not None:
                mock_get.side_effect = side
                mock_get.return_value = None
            else:
                mock_get.side_effect = None
                mock_get.return_value = ApiResponse(
                    HttpResponse(
                        status_code=500,
                        reason_phrase="err",
                        headers=[],
                        text="not-json",
                        request=None,
                    ),
                    None,
                )
            try:
                ok, msg = refresh_payment(self.test_order)
            except Exception as exc:
                self.fail(f"refresh_payment raised: {exc!r}")
            self.assertFalse(ok)
            self.assertIsInstance(msg, str)


# ---------------------------------------------------------------------------
# A.4 - update_order_payment_data
# ---------------------------------------------------------------------------


@tag("paypal")
class TestUpdateOrderPaymentData(OrdersTestCase):
    """Matrix section A.4 - exercises update_order_payment_data()."""

    def _order(self):
        o = Order(total=0, status=Order.PENDING, reference="R")
        o.save()
        return o

    @staticmethod
    def _payment(status, value="50.00"):
        return CapturedPayment.from_dictionary(
            {
                "id": "cap",
                "status": status,
                "amount": {"currency_code": "USD", "value": value},
            }
        )

    def test_A4_1_status_mapping(self):
        cases = [
            (CaptureStatus.COMPLETED, Order.COMPLETED),
            (CaptureStatus.DECLINED, Order.FAILED),
            (CaptureStatus.PARTIALLY_REFUNDED, Order.REFUNDED),
            (CaptureStatus.PENDING, Order.PENDING),
            (CaptureStatus.REFUNDED, Order.REFUNDED),
            (CaptureStatus.FAILED, Order.FAILED),
        ]
        for paypal_status, expected_status in cases:
            with self.subTest(paypal_status=paypal_status):
                order = self._order()
                update_order_payment_data(order, 0, self._payment(paypal_status))
                self.assertEqual(expected_status, order.status)

    def test_A4_2_returned_total(self):
        order = self._order()
        # COMPLETED returns payment amount
        total = update_order_payment_data(order, 0, self._payment(CaptureStatus.COMPLETED, "50.00"))
        self.assertEqual(50.0, total)
        # PENDING also sets total
        total = update_order_payment_data(order, 0, self._payment(CaptureStatus.PENDING, "25.00"))
        self.assertEqual(25.0, total)
        # Other statuses leave passed-in total
        total = update_order_payment_data(
            order, 123, self._payment(CaptureStatus.DECLINED, "50.00")
        )
        self.assertEqual(123, total)


# ---------------------------------------------------------------------------
# A.5 - update_order_refund_data
# ---------------------------------------------------------------------------


@tag("paypal")
class TestUpdateOrderRefundData(OrdersTestCase):
    """Matrix section A.5 - exercises update_order_refund_data()."""

    def _order(self, total=50, org=0, charity=0):
        o = Order(
            total=total,
            status=Order.COMPLETED,
            reference="UPDR",
            orgDonation=org,
            charityDonation=charity,
            notes="",
        )
        o.save()
        return o

    @staticmethod
    def _refund(status, value="5.00"):
        return Refund.from_dictionary(
            {
                "id": "r",
                "status": status,
                "amount": {"currency_code": "USD", "value": value},
            }
        )

    def test_A5_1_all_pending(self):
        order = self._order()
        update_order_refund_data(order, [self._refund(RefundStatus.PENDING)])
        self.assertEqual(Order.REFUND_PENDING, order.status)

    def test_A5_2_all_completed(self):
        order = self._order()
        update_order_refund_data(order, [self._refund(RefundStatus.COMPLETED)])
        self.assertEqual(Order.REFUNDED, order.status)

    def test_A5_3_mixed_pending_wins(self):
        order = self._order()
        update_order_refund_data(
            order,
            [
                self._refund(RefundStatus.COMPLETED),
                self._refund(RefundStatus.PENDING),
            ],
        )
        self.assertEqual(Order.REFUND_PENDING, order.status)

    def test_A5_4_empty_refunds(self):
        order = self._order()
        original_status = order.status
        msg = update_order_refund_data(order, [])
        self.assertEqual(original_status, order.status)
        self.assertIsNone(msg)

    def test_A5_5_donation_reset_side_effect(self):
        # total=10, donations sum to 30 -> must reset.
        order = self._order(total=10, org=15, charity=15)
        msg = update_order_refund_data(order, [self._refund(RefundStatus.COMPLETED)])
        self.assertEqual(0, order.orgDonation)
        self.assertEqual(order.total, order.charityDonation)
        self.assertIsNotNone(msg)
        self.assertIn("reset", msg)

    def test_A5_6_no_reset_when_total_sufficient(self):
        order = self._order(total=50, org=5, charity=5)
        msg = update_order_refund_data(order, [self._refund(RefundStatus.COMPLETED)])
        self.assertIsNone(msg)
        self.assertEqual(5, order.orgDonation)
        self.assertEqual(5, order.charityDonation)


# ---------------------------------------------------------------------------
# A.6 - refund_payment dispatcher
# ---------------------------------------------------------------------------


@tag("paypal")
@tag("refund")
class TestRefundPaymentDispatcher(OrdersTestCase):
    """Matrix section A.6 - exercises refund_payment() dispatcher."""

    def _order(self, billing_type=Order.CREDIT, status=Order.COMPLETED):
        o = Order(total=50, status=status, reference="DISP", billingType=billing_type)
        o.save()
        return o

    def test_A6_1_failed_order_rejected(self):
        order = self._order(status=Order.FAILED)
        ok, message = refund_payment(order, 10, "r")
        self.assertFalse(ok)
        self.assertIn("Failed orders", message)

    @patch("registration.paypal_payments.refund_card_payment")
    def test_A6_2_credit_delegates(self, mock_card):
        mock_card.return_value = (True, "delegated-ok")
        order = self._order(billing_type=Order.CREDIT)
        ok, message = refund_payment(order, 10, "r")
        self.assertTrue(ok)
        self.assertEqual("delegated-ok", message)
        mock_card.assert_called_once()

    @patch("registration.paypal_payments.refund_cash_payment")
    def test_A6_3_cash_delegates(self, mock_cash):
        mock_cash.return_value = (True, None)
        order = self._order(billing_type=Order.CASH)
        ok, _message = refund_payment(order, 10, "r")
        self.assertTrue(ok)
        mock_cash.assert_called_once()

    def test_A6_4_comp_rejected(self):
        order = self._order(billing_type=Order.COMP)
        ok, message = refund_payment(order, 10, "r")
        self.assertFalse(ok)
        self.assertIn("Comped", message)

    def test_A6_5_unpaid_rejected(self):
        order = self._order(billing_type=Order.UNPAID)
        ok, message = refund_payment(order, 10, "r")
        self.assertFalse(ok)
        self.assertIn("Unpaid", message)

    def test_A6_6_unknown_billing_type(self):
        order = self._order()
        order.billingType = "Bitcoin"  # not a real choice
        ok, message = refund_payment(order, 10, "r")
        self.assertFalse(ok)
        self.assertIn("Not sure how to refund", message)


# ---------------------------------------------------------------------------
# A.7 - refund_card_payment (extends existing TestPayPalRefunds)
# ---------------------------------------------------------------------------


@tag("paypal")
@tag("refund")
class TestPayPalRefunds(OrdersTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.event = Event(**DEFAULT_EVENT_ARGS)
        self.event.save()

        self.full_refund_body = generate_refund_mock(
            "SUCCESS_FULL",
            RefundStatus.COMPLETED,
            self.price_45.basePrice,
            "full refund",
        )
        self.part_refund_10_body = generate_refund_mock(
            "SUCCESS_PARTIAL_10", RefundStatus.COMPLETED, 10, "ten dollars off"
        )
        self.part_refund_half_body = generate_refund_mock(
            "SUCCESS_PARTIAL_HALF",
            RefundStatus.COMPLETED,
            self.price_45.basePrice / 2,
            "half off",
        )

        self.order_no_refund = Order(total=self.price_45.basePrice)
        self.order_no_refund.apiData = {
            "id": "no_refund",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {
                        "currency_code": "USD",
                        "value": str(self.price_45.basePrice),
                    },
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "id4",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": f"{self.price_45.basePrice:.2f}",
                                },
                            }
                        ]
                    },
                }
            ],
        }
        self.order_no_refund.save()

        self.order_full_refund = Order(total=self.price_45.basePrice)
        self.order_full_refund.apiData = {
            "id": "full_refund",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {
                        "currency_code": "USD",
                        "value": str(self.price_45.basePrice),
                    },
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "successful_capture",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": f"{self.price_45.basePrice:.2f}",
                                },
                            }
                        ],
                        "refunds": [
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "successful_full_refund",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": f"{self.price_45.basePrice:.2f}",
                                },
                            }
                        ],
                    },
                }
            ],
        }
        self.order_full_refund.save()

        self.order_partial_refund = Order(total=self.price_45.basePrice)
        self.order_partial_refund.apiData = {
            "id": "partial_refund",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{self.price_45.basePrice:.2f}",
                    },
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "successful_capture",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": f"{self.price_45.basePrice:.2f}",
                                },
                            }
                        ],
                        "refunds": [
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "successful_10_refund_1",
                                "amount": {"currency_code": "USD", "value": "10.00"},
                            }
                        ],
                    },
                }
            ],
        }
        self.order_partial_refund.save()

        self.order_multi_refund = Order(total=self.price_45.basePrice)
        self.order_multi_refund.apiData = {
            "id": "multi_refund",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{self.price_45.basePrice:.2f}",
                    },
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "successful_capture",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": f"{self.price_45.basePrice:.2f}",
                                },
                            }
                        ],
                        "refunds": [
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "successful_half_refund_1",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % (self.price_45.basePrice / 2),
                                },
                            },
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "successful_half_refund_2",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % (self.price_45.basePrice / 2),
                                },
                            },
                        ],
                    },
                }
            ],
        }
        self.order_multi_refund.save()

    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_first_full_refund(self, mock_refund_captured_payment: Mock):
        mock_refund_captured_payment.return_value = create_api_response(
            self.full_refund_body, Refund, 201
        )

        result, message = refund_card_payment(
            self.order_no_refund, self.price_45.basePrice, "some reason"
        )

        mock_refund_captured_payment.assert_called_once()
        self.assertTrue(result)
        self.assertEqual(message, "PayPal refund has been submitted and is COMPLETED")

        self.order_no_refund.refresh_from_db()

        self.assertEqual(0, self.order_no_refund.total)
        api_data = cast(dict[str, Any], self.order_no_refund.apiData)
        self.assertTrue("refunds" in api_data["purchase_units"][0]["payments"])
        refund_list = api_data["purchase_units"][0]["payments"]["refunds"]
        self.assertEqual(1, len(refund_list))
        refund_data = refund_list[0]
        self.assertEqual("SUCCESS_FULL", refund_data["id"])
        self.assertEqual(f"{self.price_45.basePrice:.2f}", refund_data["amount"]["value"])
        self.assertEqual(RefundStatus.COMPLETED, refund_data["status"])
        self.assertEqual("full refund", refund_data["note_to_payer"])

    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_first_partial_refund(self, mock_refund_captured_payment: Mock):
        mock_refund_captured_payment.return_value = create_api_response(
            self.part_refund_half_body, Refund, 201
        )

        result, message = refund_card_payment(
            self.order_no_refund, self.price_45.basePrice / 2, "some reason"
        )

        mock_refund_captured_payment.assert_called_once()
        self.assertTrue(result)
        self.assertEqual(message, "PayPal refund has been submitted and is COMPLETED")

        self.order_no_refund.refresh_from_db()

        self.assertEqual(self.price_45.basePrice / 2, self.order_no_refund.total)
        api_data = cast(dict[str, Any], self.order_no_refund.apiData)
        self.assertTrue("refunds" in api_data["purchase_units"][0]["payments"])
        refund_list = api_data["purchase_units"][0]["payments"]["refunds"]
        self.assertEqual(1, len(refund_list))
        refund_data = refund_list[0]
        self.assertEqual("SUCCESS_PARTIAL_HALF", refund_data["id"])
        self.assertEqual("%.2f" % (self.price_45.basePrice / 2), refund_data["amount"]["value"])
        self.assertEqual(RefundStatus.COMPLETED, refund_data["status"])
        self.assertEqual("half off", refund_data["note_to_payer"])

    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_first_multiple_refunds(self, mock_refund_captured_payment: Mock):
        mock_refund_captured_payment.return_value = create_api_response(
            self.part_refund_10_body, Refund, 201
        )
        # The shared fixture builds PriceLevel/Order in-memory with float
        # money literals (e.g. basePrice=45.00); `.save()` does not coerce
        # the in-memory instance, so .basePrice / .total stay Python
        # floats. Production ALWAYS reads these back from the DB as the
        # DecimalField's Decimal. Reload both so every money value in this
        # test is the realistic Decimal — matching the Decimal `amount`
        # contract of refund_card_payment (sibling refund tests
        # refresh_from_db() likewise).
        self.order_no_refund.refresh_from_db()
        self.price_45.refresh_from_db()
        result, message = refund_card_payment(self.order_no_refund, Decimal(10), "some reason")

        mock_refund_captured_payment.assert_called_once()
        self.assertTrue(result)
        self.assertEqual(message, "PayPal refund has been submitted and is COMPLETED")

        mock_refund_captured_payment.return_value = create_api_response(
            self.part_refund_half_body, Refund, 201
        )
        result, message = refund_card_payment(
            self.order_no_refund, self.price_45.basePrice / 2, "some reason"
        )

        self.assertEqual(2, len(mock_refund_captured_payment.mock_calls))
        self.assertTrue(result)
        self.assertEqual(message, "PayPal refund has been submitted and is COMPLETED")

        self.order_no_refund.refresh_from_db()

        self.assertEqual(
            self.price_45.basePrice - (self.price_45.basePrice / 2) - 10,
            self.order_no_refund.total,
        )
        api_data = cast(dict[str, Any], self.order_no_refund.apiData)
        self.assertTrue("refunds" in api_data["purchase_units"][0]["payments"])
        refund_list = api_data["purchase_units"][0]["payments"]["refunds"]
        self.assertEqual(2, len(refund_list))

        self.assertEqual("SUCCESS_PARTIAL_10", refund_list[0]["id"])
        self.assertEqual(f"{10:.2f}", refund_list[0]["amount"]["value"])
        self.assertEqual(RefundStatus.COMPLETED, refund_list[0]["status"])
        self.assertEqual("ten dollars off", refund_list[0]["note_to_payer"])

        self.assertEqual("SUCCESS_PARTIAL_HALF", refund_list[1]["id"])
        self.assertEqual("%.2f" % (self.price_45.basePrice / 2), refund_list[1]["amount"]["value"])
        self.assertEqual(RefundStatus.COMPLETED, refund_list[1]["status"])
        self.assertEqual("half off", refund_list[1]["note_to_payer"])

    # ------------------------------------------------------------------ A7.4
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_4_amount_exceeds_available(self, mock_refund):
        ok, message = refund_card_payment(self.order_no_refund, self.price_45.basePrice * 2, "r")
        self.assertFalse(ok)
        self.assertIn("cannot exceed available", message)
        mock_refund.assert_not_called()

    # ------------------------------------------------------------------ A7.5
    def test_A7_5_already_fully_refunded(self):
        ok, message = refund_card_payment(self.order_full_refund, 5, "r")
        self.assertFalse(ok)
        self.assertIn("already been refunded in full", message)

    # ------------------------------------------------------------------ A7.6
    def test_A7_6_malformed_apidata(self):
        # api_data that yields None from from_dictionary -> None apiData dict.
        order = Order(total=45, apiData=None)
        order.save()
        ok, message = refund_card_payment(order, 5, "r")
        self.assertFalse(ok)
        self.assertIn("malformed or missing order data", message)

    # ------------------------------------------------------------------ A7.7
    def test_A7_7_missing_capture_data(self):
        order = Order(total=45)
        order.apiData = {"id": "nope", "purchase_units": []}
        order.save()
        ok, message = refund_card_payment(order, 5, "r")
        self.assertFalse(ok)
        self.assertIn("Can't find payment capture data", message)

    # ------------------------------------------------------------------ A7.8
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_8_refund_status_pending(self, mock_refund):
        pending_body = generate_refund_mock("PEND", RefundStatus.PENDING, 10, "pending")
        mock_refund.return_value = create_api_response(pending_body, Refund, 201)
        ok, _message = refund_card_payment(self.order_no_refund, 10, "r")
        self.assertTrue(ok)
        self.order_no_refund.refresh_from_db()
        self.assertEqual(Order.REFUND_PENDING, self.order_no_refund.status)
        self.assertEqual(self.price_45.basePrice - 10, self.order_no_refund.total)

    # ------------------------------------------------------------------ A7.9
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_9_refund_status_cancelled(self, mock_refund):
        body = generate_refund_mock("CAN", RefundStatus.CANCELLED, 10, "x")
        mock_refund.return_value = create_api_response(body, Refund, 201)
        self.order_no_refund.status = Order.PENDING
        self.order_no_refund.save()
        ok, _ = refund_card_payment(self.order_no_refund, 10, "r")
        self.assertTrue(ok)
        self.order_no_refund.refresh_from_db()
        self.assertEqual(Order.COMPLETED, self.order_no_refund.status)
        self.assertEqual(self.price_45.basePrice, self.order_no_refund.total)

    # ------------------------------------------------------------------ A7.10
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_10_refund_status_failed(self, mock_refund):
        body = generate_refund_mock("FLD", RefundStatus.FAILED, 10, "x")
        mock_refund.return_value = create_api_response(body, Refund, 201)
        self.order_no_refund.status = Order.PENDING
        self.order_no_refund.save()
        ok, _ = refund_card_payment(self.order_no_refund, 10, "r")
        self.assertTrue(ok)
        self.order_no_refund.refresh_from_db()
        self.assertEqual(Order.COMPLETED, self.order_no_refund.status)
        self.assertEqual(self.price_45.basePrice, self.order_no_refund.total)

    # ------------------------------------------------------------------ A7.11
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_11_api_exception_uses_ex_response(self, mock_refund):
        # TODO: SDK error-body shape for refund_captured_payment exceptions
        # is unclear. Build a Mock(spec=ApiResponse) directly with just the
        # attributes production touches: .text, .is_error(), .body. Production
        # does api_response = e.response then json.loads(api_response.text)
        # and api_response.is_error().
        err_body = "Error in PayPal refund: boom"
        response = Mock(spec=ApiResponse)
        response.is_error = Mock(return_value=True)
        response.text = json.dumps(err_body)
        response.body = None
        exc = ApiException.__new__(ApiException)
        exc.reason = "boom"
        exc.response = response
        exc.response_code = 422
        mock_refund.side_effect = exc

        try:
            ok, message = refund_card_payment(self.order_no_refund, 10, "r")
        except Exception as e:
            self.fail(f"refund_card_payment raised: {e!r}")
        self.assertFalse(ok)
        # message is the errors list from body
        self.assertEqual(err_body, message)

    # ------------------------------------------------------------------ A7.12
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_12_api_is_error(self, mock_refund):
        err_body = {"errors": [{"issue": "INTERNAL"}]}
        mock_refund.return_value = ApiResponse(
            HttpResponse(
                status_code=500,
                reason_phrase="oops",
                headers=[],
                text=json.dumps(err_body),
                request=None,
            ),
            None,
        )
        ok, message = refund_card_payment(self.order_no_refund, 10, "r")
        self.assertFalse(ok)
        self.assertEqual(err_body["errors"], message)

    # ------------------------------------------------------------------ A7.13
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_13_full_refund_sends_no_body(self, mock_refund):
        mock_refund.return_value = create_api_response(self.full_refund_body, Refund, 201)
        refund_card_payment(self.order_no_refund, self.price_45.basePrice, "full")
        sent_args = mock_refund.call_args[0][0]
        self.assertNotIn("body", sent_args)
        self.assertIn("capture_id", sent_args)

    # ------------------------------------------------------------------ A7.14
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_14_refund_resets_donations_direct(self, mock_refund):
        self.order_no_refund.orgDonation = 20
        self.order_no_refund.charityDonation = 20
        self.order_no_refund.notes = ""
        self.order_no_refund.save()
        # refund most of the total so remaining < donations
        refund_amount = self.price_45.basePrice - 5
        body = generate_refund_mock("RESET", RefundStatus.COMPLETED, refund_amount, "r")
        mock_refund.return_value = create_api_response(body, Refund, 201)

        refund_card_payment(self.order_no_refund, refund_amount, "r")

        self.order_no_refund.refresh_from_db()
        self.assertEqual(0, self.order_no_refund.orgDonation)
        self.assertEqual(0, self.order_no_refund.charityDonation)
        self.assertIn("reset", self.order_no_refund.notes)

    # ------------------------------------------------------------------ A7.15
    @override_settings(PAYPAL_CURRENCY="EUR")
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_15_respects_paypal_currency(self, mock_refund):
        mock_refund.return_value = create_api_response(self.part_refund_half_body, Refund, 201)
        refund_card_payment(self.order_no_refund, self.price_45.basePrice / 2, "r")
        sent_args = mock_refund.call_args[0][0]
        body = sent_args["body"]
        self.assertEqual("EUR", body.amount.currency_code)

    # ------------------------------------------------------------------ A7.16
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_16_reason_none(self, mock_refund):
        mock_refund.return_value = create_api_response(self.part_refund_half_body, Refund, 201)
        try:
            ok, _ = refund_card_payment(self.order_no_refund, self.price_45.basePrice / 2, None)
        except Exception as e:
            self.fail(f"refund_card_payment raised with reason=None: {e!r}")
        self.assertTrue(ok)

    # ------------------------------------------------------------------ A7.17
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_17_no_refunds_key_shape(self, mock_refund):
        order = Order(
            total=99.99,
            apiData=paypal_apidata_no_refunds(capture_amount="99.99"),
        )
        order.save()
        # Explicit: refunds key is missing from payments dict.
        self.assertNotIn("refunds", order.apiData["purchase_units"][0]["payments"])
        mock_refund.return_value = create_api_response(
            generate_refund_mock("P", RefundStatus.COMPLETED, 10, "r"),
            Refund,
            201,
        )
        ok, _ = refund_card_payment(order, 10, "r")
        self.assertTrue(ok)

    # ------------------------------------------------------------------ A7.18
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_18_partial_then_partial_against_remaining(self, mock_refund):
        order = Order(
            total=89.99,  # 99.99 less first 10.00 refund
            apiData=paypal_apidata_one_partial_refund(amount="10.00", capture_amount="99.99"),
        )
        order.save()
        mock_refund.return_value = create_api_response(
            generate_refund_mock("P2", RefundStatus.COMPLETED, 50, "r"),
            Refund,
            201,
        )
        ok, _ = refund_card_payment(order, 50, "r")
        self.assertTrue(ok)

    # ------------------------------------------------------------------ A7.19
    def test_A7_19_prior_full_refund_rejected(self):
        order = Order(
            total=0,
            apiData=paypal_apidata_fully_refunded(capture_amount="99.99"),
        )
        order.save()
        ok, message = refund_card_payment(order, 5, "r")
        self.assertFalse(ok)
        self.assertIn("already been refunded in full", message)

    # ------------------------------------------------------------------ A7.20
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_20_multi_partial_then_partial(self, mock_refund):
        order = Order(
            total=79.99,  # 99.99 minus 20 prior refunds
            apiData=paypal_apidata_multi_partial(["5.00", "15.00"], capture_amount="99.99"),
        )
        order.save()
        mock_refund.return_value = create_api_response(
            generate_refund_mock("P3", RefundStatus.COMPLETED, 20, "r"),
            Refund,
            201,
        )
        ok, _ = refund_card_payment(order, 20, "r")
        self.assertTrue(ok)

    # ------------------------------------------------------------------ A7.21
    def test_A7_21_multi_refunds_sum_full_rejected(self):
        order = Order(
            total=0,
            apiData=paypal_apidata_multi_full(["49.99", "50.00"], capture_amount="99.99"),
        )
        order.save()
        ok, message = refund_card_payment(order, 5, "r")
        self.assertFalse(ok)
        self.assertIn("already been refunded in full", message)

    # ------------------------------------------------------------------ A7.22
    def test_A7_22_apidata_none(self):
        order = Order(total=45, apiData=None)
        order.save()
        ok, message = refund_card_payment(order, 5, "r")
        self.assertFalse(ok)
        self.assertIn("malformed or missing order data", message)

    # ------------------------------------------------------------------ A7.23
    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_A7_23_never_raises_contract(self, mock_refund):
        # Every error path must return a (bool, str|list) tuple, no raises.
        cases = [
            # ApiException with minimal response stub
            lambda: self._raise_variant(mock_refund),
            # is_error response
            lambda: self._is_error_variant(mock_refund),
            # malformed apiData
            lambda: self._malformed_variant(),
            # already fully refunded
            lambda: self._fully_refunded_variant(),
        ]
        for case in cases:
            try:
                ok, _msg = case()
            except Exception as e:
                self.fail(f"refund_card_payment raised: {e!r}")
            self.assertFalse(ok)

    def _raise_variant(self, mock_refund):
        err_body = {"errors": [{"issue": "INTERNAL"}]}
        response = Mock(spec=ApiResponse)
        response.is_error = Mock(return_value=True)
        response.text = json.dumps(err_body)
        response.body = None
        exc = ApiException.__new__(ApiException)
        exc.reason = "boom"
        exc.response = response
        exc.response_code = 500
        mock_refund.side_effect = exc
        order = Order(
            total=45,
            apiData=paypal_apidata_no_refunds(capture_amount="45.00"),
        )
        order.save()
        return refund_card_payment(order, 10, "r")

    def _is_error_variant(self, mock_refund):
        mock_refund.side_effect = None
        err_body = {"errors": [{"issue": "INTERNAL"}]}
        mock_refund.return_value = ApiResponse(
            HttpResponse(
                status_code=500,
                reason_phrase="oops",
                headers=[],
                text=json.dumps(err_body),
                request=None,
            ),
            None,
        )
        order = Order(
            total=45,
            apiData=paypal_apidata_no_refunds(capture_amount="45.00"),
        )
        order.save()
        return refund_card_payment(order, 10, "r")

    def _malformed_variant(self):
        order = Order(total=45, apiData=None)
        order.save()
        return refund_card_payment(order, 5, "r")

    def _fully_refunded_variant(self):
        order = Order(
            total=0,
            apiData=paypal_apidata_fully_refunded(capture_amount="45.00"),
        )
        order.save()
        return refund_card_payment(order, 5, "r")


# ---------------------------------------------------------------------------
# A.8 - get_available_refund_amount
# ---------------------------------------------------------------------------


@tag("paypal")
class TestGetAvailableRefundAmount(OrdersTestCase):
    """Matrix section A.8 - exercises get_available_refund_amount()."""

    def test_A8_1_single_completed_no_refunds(self):
        order = PayPalOrder.from_dictionary(paypal_apidata_no_refunds(capture_amount="50.00"))
        self.assertEqual(50.0, get_available_refund_amount(order))

    def test_A8_2_capture_with_completed_refund(self):
        order = PayPalOrder.from_dictionary(
            paypal_apidata_one_partial_refund(amount="10.00", capture_amount="50.00")
        )
        self.assertEqual(40.0, get_available_refund_amount(order))

    def test_A8_3_partial_and_refunded_statuses_count(self):
        for status in (
            CaptureStatus.PARTIALLY_REFUNDED,
            CaptureStatus.REFUNDED,
        ):
            with self.subTest(status=status):
                data = paypal_apidata_no_refunds(capture_status=status, capture_amount="50.00")
                order = PayPalOrder.from_dictionary(data)
                self.assertEqual(50.0, get_available_refund_amount(order))

    def test_A8_4_excluded_statuses_do_not_count(self):
        for status in (
            CaptureStatus.DECLINED,
            CaptureStatus.FAILED,
            CaptureStatus.PENDING,
        ):
            with self.subTest(status=status):
                data = paypal_apidata_no_refunds(capture_status=status, capture_amount="50.00")
                order = PayPalOrder.from_dictionary(data)
                self.assertEqual(0.0, get_available_refund_amount(order))

    def test_A8_5_pending_refund_does_not_reduce(self):
        data = paypal_apidata_no_refunds(capture_amount="50.00")
        data["purchase_units"][0]["payments"]["refunds"] = [
            make_refund_dict(id="r", status="PENDING", amount="10.00")
        ]
        order = PayPalOrder.from_dictionary(data)
        self.assertEqual(50.0, get_available_refund_amount(order))

    def test_A8_6_multiple_purchase_units_summed(self):
        data = paypal_apidata_no_refunds(capture_amount="50.00")
        # Clone the purchase unit to create a second one
        second_unit = json.loads(json.dumps(data["purchase_units"][0]))
        second_unit["reference_id"] = "donations"
        second_unit["payments"]["captures"][0]["id"] = "cap2"
        data["purchase_units"].append(second_unit)
        order = PayPalOrder.from_dictionary(data)
        self.assertEqual(100.0, get_available_refund_amount(order))

    def test_A8_7_missing_refunds_attribute(self):
        # The default no-refunds shape produces a PaymentCollection without
        # the ``refunds`` attribute — that's exactly the hasattr-guarded path.
        data = paypal_apidata_no_refunds(capture_amount="50.00")
        order = PayPalOrder.from_dictionary(data)
        self.assertFalse(hasattr(order.purchase_units[0].payments, "refunds"))
        self.assertEqual(50.0, get_available_refund_amount(order))


# ---------------------------------------------------------------------------
# A.9 - coverage gap fills for paypal_payments.py
# ---------------------------------------------------------------------------


@tag("paypal")
class TestPayPalPaymentsCoverageGaps(OrdersTestCase):
    """Branches not reached by the A.1-A.8 matrix:

    * format_errors description-present branch (L92)
    * create_unpaid_paypal_order with apis_reference propagated (L151-152)
    * capture_paypal_payment non-JSON ApiException body (L200-201)
    * capture_paypal_payment non-JSON success body (L216-220)
    """

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.create_order")
    def test_A9_1_apis_reference_sets_invoice_and_custom_ids(self, mock_create):
        """When apis_reference is provided it must be mirrored on the purchase
        unit as both invoice_id and custom_id so PayPal can echo it back on
        every downstream capture/refund/dispute event."""
        body = {
            "id": "ORD-NEW",
            "status": "CREATED",
            "purchase_units": [
                {
                    "reference_id": "registration",
                    "amount": {"currency_code": "USD", "value": "45.00"},
                }
            ],
        }
        mock_create.return_value = create_api_response(body, PayPalOrder, 201)

        create_unpaid_paypal_order(
            total="45.00",
            discount="0",
            cart_items=[{"name": "Reg", "total": "45.00", "donation": False}],
            apis_reference="APIS-REF-123",
        )

        call_args = mock_create.call_args[0][0]
        pu = call_args["body"].purchase_units[0]
        self.assertEqual("APIS-REF-123", pu.invoice_id)
        self.assertEqual("APIS-REF-123", pu.custom_id)

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A9_2_capture_api_exception_non_json_body(self, mock_capture):
        """ApiException whose .response.text is not JSON must fall through the
        except(ValueError, JSONDecodeError) branch and return the generic
        unknown-error message."""
        response = Mock(spec=HttpResponse)
        response.status_code = 500
        response.text = "<html><body>PayPal gateway error</body></html>"
        exc = ApiException("PayPal API failure", response)
        mock_capture.side_effect = exc

        apis_order = Order(total=95, status=Order.PENDING, reference="CAP-NONJSON")
        apis_order.save()

        ok, body = capture_paypal_payment("ABCDEF", apis_order)

        self.assertFalse(ok)
        self.assertIn("errors", body)
        self.assertEqual(
            ["An unknown PayPal error occurred during payment capture"],
            body["errors"],
        )

    @patch("paypalserversdk.controllers.orders_controller.OrdersController.capture_order")
    def test_A9_3_capture_success_non_json_text_body(self, mock_capture):
        """A successful ApiResponse (is_error() False) whose .text is not
        valid JSON must hit the JSONDecodeError branch and return an error
        tuple without persisting apiData."""
        bogus_response = Mock(spec=ApiResponse)
        bogus_response.is_error = Mock(return_value=False)
        bogus_response.text = "not actually json"
        mock_capture.return_value = bogus_response

        apis_order = Order(total=95, status=Order.PENDING, reference="CAP-BADJSON")
        apis_order.save()

        ok, body = capture_paypal_payment("ABCDEF", apis_order)

        self.assertFalse(ok)
        self.assertIn("errors", body)
        self.assertIn("Unable to decode response from PayPal", body["errors"][0])

    def test_A9_4_format_errors_includes_description_when_present(self):
        """format_errors appends the description when the ErrorDetails object
        carries one — the hasattr(error, "description") true branch."""
        from registration.paypal_payments import format_errors

        stub = Mock(spec=ApiResponse)
        stub.text = json.dumps(
            {
                "errors": [
                    {
                        "issue": "INSTRUMENT_DECLINED",
                        "description": "Card was declined",
                    }
                ]
            }
        )

        result = format_errors(stub)

        self.assertIn("INSTRUMENT_DECLINED", result)
        self.assertIn("Card was declined", result)
