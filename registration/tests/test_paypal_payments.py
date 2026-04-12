import json
from typing import Optional, Type
from unittest.mock import Mock, patch

from django.test import tag
from paypalserversdk.api_helper import APIHelper
from paypalserversdk.http.api_response import ApiResponse
from paypalserversdk.http.http_response import HttpResponse
from paypalserversdk.models.capture_status import CaptureStatus
from paypalserversdk.models.order import Order as PayPalOrder
from paypalserversdk.models.refund import Refund
from paypalserversdk.models.refund_status import RefundStatus

from registration.models import Attendee, Event, Order, PriceLevel
from registration.paypal_payments import refresh_payment, refund_card_payment
from registration.tests.common import (
    DEFAULT_EVENT_ARGS,
    TEST_ATTENDEE_ARGS,
    OrdersTestCase,
)


def generate_refund_mock(
    id: str, status: RefundStatus, amount: float, reason: str
) -> str:
    return """{
        "id": "%(id)s",
        "amount": {
            "value": "%(amount).2f",
            "currency_code": "USD"
        },
        "status": "%(status)s",
        "note_to_payer": "%(reason)s",
        "create_time": "2018-09-11T23:24:19Z",
        "update_time": "2018-09-11T23:24:19Z"
    }""" % locals()


def create_api_response(
    body: str | dict, body_type: Type, code: Optional[int] = 200
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
                            ]
                        },
                        "refunds": [
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "test_order_refund",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % self.price_45.basePrice,
                                },
                            }
                        ],
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
                            ]
                        },
                        "refunds": [
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "test_order_refund",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % self.price_45.basePrice,
                                },
                            }
                        ],
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
                                    "value": "%.2f" % self.price_45.basePrice,
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
                                    "value": "%.2f" % self.price_45.basePrice,
                                },
                            }
                        ],
                        "refunds": [
                            {
                                "status": RefundStatus.COMPLETED,
                                "id": "successful_full_refund",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % self.price_45.basePrice,
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
                        "value": "%.2f" % self.price_45.basePrice,
                    },
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "successful_capture",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % self.price_45.basePrice,
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
                        "value": "%.2f" % self.price_45.basePrice,
                    },
                    "payments": {
                        "captures": [
                            {
                                "status": CaptureStatus.COMPLETED,
                                "id": "successful_capture",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "%.2f" % self.price_45.basePrice,
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
        self.assertTrue(
            "refunds" in self.order_no_refund.apiData["purchase_units"][0]["payments"]
        )
        refund_list = self.order_no_refund.apiData["purchase_units"][0]["payments"][
            "refunds"
        ]
        self.assertEqual(1, len(refund_list))
        refund_data = refund_list[0]
        self.assertEqual("SUCCESS_FULL", refund_data["id"])
        self.assertEqual(
            "%.2f" % self.price_45.basePrice, refund_data["amount"]["value"]
        )
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
        self.assertTrue(
            "refunds" in self.order_no_refund.apiData["purchase_units"][0]["payments"]
        )
        refund_list = self.order_no_refund.apiData["purchase_units"][0]["payments"][
            "refunds"
        ]
        self.assertEqual(1, len(refund_list))
        refund_data = refund_list[0]
        self.assertEqual("SUCCESS_PARTIAL_HALF", refund_data["id"])
        self.assertEqual(
            "%.2f" % (self.price_45.basePrice / 2), refund_data["amount"]["value"]
        )
        self.assertEqual(RefundStatus.COMPLETED, refund_data["status"])
        self.assertEqual("half off", refund_data["note_to_payer"])

    @patch(
        "paypalserversdk.controllers.payments_controller.PaymentsController.refund_captured_payment"
    )
    def test_first_multiple_refunds(self, mock_refund_captured_payment: Mock):
        mock_refund_captured_payment.return_value = create_api_response(
            self.part_refund_10_body, Refund, 201
        )
        result, message = refund_card_payment(self.order_no_refund, 10, "some reason")

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
        self.assertTrue(
            "refunds" in self.order_no_refund.apiData["purchase_units"][0]["payments"]
        )
        refund_list = self.order_no_refund.apiData["purchase_units"][0]["payments"][
            "refunds"
        ]
        self.assertEqual(2, len(refund_list))

        self.assertEqual("SUCCESS_PARTIAL_10", refund_list[0]["id"])
        self.assertEqual("%.2f" % 10, refund_list[0]["amount"]["value"])
        self.assertEqual(RefundStatus.COMPLETED, refund_list[0]["status"])
        self.assertEqual("ten dollars off", refund_list[0]["note_to_payer"])

        self.assertEqual("SUCCESS_PARTIAL_HALF", refund_list[1]["id"])
        self.assertEqual(
            "%.2f" % (self.price_45.basePrice / 2), refund_list[1]["amount"]["value"]
        )
        self.assertEqual(RefundStatus.COMPLETED, refund_list[1]["status"])
        self.assertEqual("half off", refund_list[1]["note_to_payer"])
