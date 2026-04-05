import datetime as dt
from decimal import Decimal
from functools import reduce
from typing import Optional
from unittest import SkipTest

from django.conf import settings
from django.test import TestCase, tag
from paypalserversdk.configuration import Environment
from paypalserversdk.controllers.vault_controller import VaultController
from paypalserversdk.http.api_response import ApiResponse
from paypalserversdk.models.card_request import CardRequest
from paypalserversdk.models.order import Order as PayPalOrder
from paypalserversdk.models.payment_token_request_payment_source import (
    PaymentTokenRequestPaymentSource,
)
from paypalserversdk.models.purchase_unit import PurchaseUnit

from registration.models import Order as ApisOrder
from registration.payments import (
    TranslatedCartItem,
    client,
    create_unpaid_paypal_order,
    orders_controller,
)

# These testing card numbers are courtesy of PayPal Sandbox
# https://developer.paypal.com/tools/sandbox/card-testing/#link-teststaticcardnumbers
testCards: list[CardRequest] = [
    CardRequest(
        name="John Doe",
        number="4005519200000004",  # Visa
        security_code="123",
        expiry=(dt.date.today() + dt.timedelta(days=365)).strftime("%Y-%m"),
    ),
    CardRequest(
        name="John Doe",
        number="4012000033330026",  # Visa
        security_code="123",
        expiry=(dt.date.today() + dt.timedelta(days=365)).strftime("%Y-%m"),
    ),
    CardRequest(
        name="John Doe",
        number="4012000077777777",  # Visa
        security_code="123",
        expiry=(dt.date.today() + dt.timedelta(days=365)).strftime("%Y-%m"),
    ),
    CardRequest(
        name="John Doe",
        number="2223000048400011",  # MasterCard
        security_code="123",
        expiry=(dt.date.today() + dt.timedelta(days=365)).strftime("%Y-%m"),
    ),
    CardRequest(
        name="John Doe",
        number="371449635398431",  # AmEx
        security_code="1234",
        expiry=(dt.date.today() + dt.timedelta(days=365)).strftime("%Y-%m"),
    ),
    CardRequest(
        name="John Doe",
        number="376680816376961",  # AmEx
        security_code="1234",
        expiry=(dt.date.today() + dt.timedelta(days=365)).strftime("%Y-%m"),
    ),
]


@tag("payment")
class PaymentsPayPalTestCase(TestCase):
    fixtures = ["test-orders"]
    paymentTokenId = None

    singleRegistation: list[TranslatedCartItem] = [
        {
            "name": "Convention 2277 Attendee - John Doe",
            "donation": False,
            "total": Decimal(50.00),
        }
    ]

    twoRegistrations: list[TranslatedCartItem] = [
        {
            "name": "Convention 2277 Attendee - John Doe",
            "donation": False,
            "total": Decimal(50.00),
        },
        {
            "name": "Convention 2277 Sponsor - Jane Donut",
            "donation": False,
            "total": Decimal(100.00),
        },
    ]

    singleRegWithDonation: list[TranslatedCartItem] = [
        {
            "name": "Convention 2277 Attendee - John Doe",
            "donation": False,
            "total": Decimal(50.00),
        },
        {
            "name": "Convention 2277 Donation",
            "donation": False,
            "total": Decimal(50.00),
        },
    ]

    twoRegistrationsWithDonation: list[TranslatedCartItem] = [
        {
            "name": "Convention 2277 Attendee - John Doe",
            "donation": False,
            "total": Decimal(50.00),
        },
        {
            "name": "Convention 2277 Sponsor - Jane Donut",
            "donation": False,
            "total": Decimal(100.00),
        },
        {
            "name": "Convention 2277 Donation",
            "donation": False,
            "total": Decimal(50.00),
        },
    ]

    @classmethod
    def check_config(cls) -> str:
        errMsg = "PayPal Server SDK Client misconfigured. "

        if client.config.environment != Environment.SANDBOX:
            return errMsg + 'PAYPAL_ENVIRONMENT must be "sandbox"'
        if not client.oauth_2.is_valid():
            return (
                errMsg
                + "Could not auth. Check settings.py or PAYPAL_* environment "
                + "variables."
            )

        return ""

    @classmethod
    def setUpClass(cls):
        super(PaymentsPayPalTestCase, cls).setUpClass()
        reason = cls.check_config()
        if reason != "":
            raise SkipTest(reason)

    def setUp(self):
        pass

    def submit_new_order(
        self, cart: list[TranslatedCartItem], discount: Optional[Decimal] = Decimal(0)
    ) -> tuple[Decimal, Decimal, ApiResponse]:
        total = reduce(lambda a, i: a + i["total"], cart, Decimal(0))
        api_response = create_unpaid_paypal_order(total, discount, cart)
        return (total, discount, api_response)

    # TESTS START HERE

    def test_create_unpaid_paypal_order_single_reg(self):
        total, discount, api_response = self.submit_new_order(self.singleRegistation)

        self.assertTrue(api_response.is_success())
        self.assertEqual(api_response.status_code, 201)
        self.assertIsInstance(api_response.body, PayPalOrder)
        api_response = orders_controller.get_order({"id": api_response.body.id})
        self.assertTrue(api_response.is_success())
        self.assertIsInstance(api_response.body, PayPalOrder)
        resp_body: PayPalOrder = api_response.body
        self.assertEqual(len(resp_body.purchase_units), 1)
        p_unit: PurchaseUnit = resp_body.purchase_units[0]
        self.assertEqual(p_unit.amount.currency_code, settings.PAYPAL_CURRENCY)
        self.assertEqual(p_unit.amount.value, "%.2f" % total)
        self.assertEqual(len(p_unit.items), 1)
        self.assertEqual(p_unit.items[0].name, self.singleRegistation[0]["name"])
        self.assertEqual(
            p_unit.items[0].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[0].unit_amount.value,
            "%.2f" % self.singleRegistation[0]["total"],
        )

    def test_create_unpaid_paypal_order_multi_reg(self):
        total, discount, api_response = self.submit_new_order(self.twoRegistrations)

        self.assertTrue(api_response.is_success())
        self.assertEqual(api_response.status_code, 201)
        self.assertIsInstance(api_response.body, PayPalOrder)
        api_response = orders_controller.get_order({"id": api_response.body.id})
        self.assertTrue(api_response.is_success())
        self.assertIsInstance(api_response.body, PayPalOrder)
        resp_body: PayPalOrder = api_response.body
        self.assertEqual(len(resp_body.purchase_units), 1)
        p_unit: PurchaseUnit = resp_body.purchase_units[0]
        self.assertEqual(p_unit.amount.currency_code, settings.PAYPAL_CURRENCY)
        self.assertEqual(p_unit.amount.value, "%.2f" % total)
        self.assertEqual(len(p_unit.items), 2)
        self.assertEqual(p_unit.items[0].name, self.twoRegistrations[0]["name"])
        self.assertEqual(
            p_unit.items[0].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[0].unit_amount.value,
            "%.2f" % self.twoRegistrations[0]["total"],
        )
        self.assertEqual(p_unit.items[1].name, self.twoRegistrations[1]["name"])
        self.assertEqual(
            p_unit.items[1].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[1].unit_amount.value,
            "%.2f" % self.twoRegistrations[1]["total"],
        )

    def test_create_unpaid_paypal_order_single_reg_with_donation(self):
        total, discount, api_response = self.submit_new_order(
            self.singleRegWithDonation
        )

        self.assertTrue(api_response.is_success())
        self.assertEqual(api_response.status_code, 201)
        self.assertIsInstance(api_response.body, PayPalOrder)
        api_response = orders_controller.get_order({"id": api_response.body.id})
        self.assertTrue(api_response.is_success())
        self.assertIsInstance(api_response.body, PayPalOrder)
        resp_body: PayPalOrder = api_response.body
        self.assertEqual(len(resp_body.purchase_units), 1)
        p_unit: PurchaseUnit = resp_body.purchase_units[0]
        self.assertEqual(p_unit.amount.currency_code, settings.PAYPAL_CURRENCY)
        self.assertEqual(p_unit.amount.value, "%.2f" % total)
        self.assertEqual(len(p_unit.items), 2)
        self.assertEqual(p_unit.items[0].name, self.singleRegWithDonation[0]["name"])
        self.assertEqual(
            p_unit.items[0].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[0].unit_amount.value,
            "%.2f" % self.singleRegWithDonation[0]["total"],
        )
        self.assertEqual(p_unit.items[1].name, self.singleRegWithDonation[1]["name"])
        self.assertEqual(
            p_unit.items[1].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[1].unit_amount.value,
            "%.2f" % self.singleRegWithDonation[1]["total"],
        )

    def test_create_unpaid_paypal_order_multi_reg_with_donation(self):
        total, discount, api_response = self.submit_new_order(
            self.twoRegistrationsWithDonation
        )

        self.assertTrue(api_response.is_success())
        self.assertEqual(api_response.status_code, 201)
        self.assertIsInstance(api_response.body, PayPalOrder)
        api_response = orders_controller.get_order({"id": api_response.body.id})
        self.assertTrue(api_response.is_success())
        self.assertIsInstance(api_response.body, PayPalOrder)
        resp_body: PayPalOrder = api_response.body
        self.assertEqual(len(resp_body.purchase_units), 1)
        p_unit: PurchaseUnit = resp_body.purchase_units[0]
        self.assertEqual(p_unit.amount.currency_code, settings.PAYPAL_CURRENCY)
        self.assertEqual(p_unit.amount.value, "%.2f" % total)
        self.assertEqual(len(p_unit.items), 3)
        self.assertEqual(
            p_unit.items[0].name, self.twoRegistrationsWithDonation[0]["name"]
        )
        self.assertEqual(
            p_unit.items[0].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[0].unit_amount.value,
            "%.2f" % self.twoRegistrationsWithDonation[0]["total"],
        )
        self.assertEqual(
            p_unit.items[1].name, self.twoRegistrationsWithDonation[1]["name"]
        )
        self.assertEqual(
            p_unit.items[1].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[1].unit_amount.value,
            "%.2f" % self.twoRegistrationsWithDonation[1]["total"],
        )
        self.assertEqual(
            p_unit.items[2].name, self.twoRegistrationsWithDonation[2]["name"]
        )
        self.assertEqual(
            p_unit.items[2].unit_amount.currency_code, settings.PAYPAL_CURRENCY
        )
        self.assertEqual(
            p_unit.items[2].unit_amount.value,
            "%.2f" % self.twoRegistrationsWithDonation[2]["total"],
        )
