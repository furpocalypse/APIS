import json
import logging
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from registration.models import *

logger = logging.getLogger(__name__)
logging.disable(logging.NOTSET)
logger.setLevel(logging.DEBUG)

tz = timezone.get_current_timezone()
now = timezone.now()
ten_days = timedelta(days=10)
one_day = timedelta(days=1)

DEFAULT_EVENT_ARGS = dict(
    default=True,
    name="Test Event 2050!",
    dealerRegStart=now - ten_days,
    dealerRegEnd=now + ten_days,
    staffRegStart=now - ten_days,
    staffRegEnd=now + ten_days,
    attendeeRegStart=now - ten_days,
    attendeeRegEnd=now + ten_days,
    onsiteRegStart=now - ten_days,
    onsiteRegEnd=now + ten_days,
    eventStart=now - ten_days,
    eventEnd=now + ten_days,
)

TEST_SIGNATURE_SVG = "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiIHN0YW5kYWxvbmU9Im5vIj8+PCFET0NUWVBFIHN2ZyBQVUJMSUMgIi0vL1czQy8vRFREIFNWRyAxLjEvL0VOIiAiaHR0cDovL3d3dy53My5vcmcvR3JhcGhpY3MvU1ZHLzEuMS9EVEQvc3ZnMTEuZHRkIj48c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgdmVyc2lvbj0iMS4xIiB3aWR0aD0iNjI3IiBoZWlnaHQ9IjkwIj48cGF0aCBzdHJva2UtbGluZWpvaW49InJvdW5kIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlPSJyZ2IoODUsIDg1LCA4NSkiIGZpbGw9Im5vbmUiIGQ9Ik0gMSA1NSBjIDAuMDUgLTAuMjEgMS4yIC04LjU5IDMgLTEyIGMgNC4yIC03Ljk1IDEwLjE2IC0xNi41NSAxNiAtMjQgYyAzLjcyIC00Ljc1IDguNDYgLTkuMjkgMTMgLTEzIGMgMi41NCAtMi4wOCA2LjE1IC0zLjk4IDkgLTUgYyAxLjM4IC0wLjQ5IDMuNzkgLTAuNjEgNSAwIGMgMi4yNCAxLjEyIDYuMzYgMy42IDcgNiBjIDMgMTEuMzEgNC40OSAyOC4zNiA2IDQzIGMgMC44NyA4LjQ0IDAuMjcgMTYuNzcgMSAyNSBjIDAuMjcgMy4wMyAxLjAzIDYuMjcgMiA5IGMgMC42MiAxLjczIDEuNjYgNC44MiAzIDUgYyA2LjE5IDAuODMgMTguMjMgMC41MyAyNyAtMSBjIDI1LjI5IC00LjQyIDQ5LjY3IC0xMS40NCA3NiAtMTcgYyAxMS4zNCAtMi4zOSAyMS44MSAtNC40IDMzIC02IGMgNS4zNSAtMC43NiAxMC41IC0wLjg2IDE2IC0xIGMgNy41NCAtMC4yIDE1LjIxIC0wLjk0IDIyIDAgYyA0LjU5IDAuNjQgOS40NyAyLjk5IDE0IDUgYyA0LjUgMiA4Ljc0IDUuMiAxMyA3IGMgMS43NiAwLjc0IDMuOTggMC45MyA2IDEgYyA4LjI5IDAuMjcgMTYuNjggMC41NSAyNSAwIGMgNi43MyAtMC40NSAyMCAtMyAyMCAtMyIvPjxwYXRoIHN0cm9rZS1saW5lam9pbj0icm91bmQiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2U9InJnYig4NSwgODUsIDg1KSIgZmlsbD0ibm9uZSIgZD0iTSAyMDUgMzggYyAwLjI1IDAgOS4zOCAwLjQ5IDE0IDAgYyA4LjAyIC0wLjg0IDE1LjgzIC0zLjIzIDI0IC00IGMgMTMuNDQgLTEuMjYgMjYuMjEgLTEuODQgNDAgLTIgYyA0NC4wOCAtMC41MiA4NC44OCAtMS43NSAxMjggMCBjIDIzLjQgMC45NSA0NS41NiA0LjMgNjkgOCBjIDI4LjUzIDQuNSA1NS4wMyAxMC4xNyA4MyAxNiBjIDQuNSAwLjk0IDguNTMgMy4xNSAxMyA0IGMgMTYuNjMgMy4xNyA1MCA4IDUwIDgiLz48L3N2Zz4="

TEST_ATTENDEE_ARGS = dict(
    firstName="Test",
    lastName="Testerson",
    address1="123 Somewhere St",
    city="Place",
    state="PA",
    country="US",
    postalCode=12345,
    phone="1112223333",
    email="apis@mailinator.org",
    birthdate="1990-01-01",
)

DEFAULT_VENUE_ARGS = dict(
    name="MegaCenter Conference Hotel",
    address="123 Somewhere St",
    city="Place",
    state="VA",
    country="US",
    postalCode=12345,
)

TEST_TABLE_ARGS = dict(
    name="Booth",
    description="description here",
    chairMin=0,
    chairMax=1,
    tableMin=0,
    tableMax=1,
    partnerMin=0,
    partnerMax=1,
    basePrice=Decimal(130),
)

TEST_DEALER_ARGS = {
    "businessName": "Something Creative",
    "website": "http://www.something.com",
    "license": "jkah9435kd",
    "nearTo": "Someone",
    "farFrom": "Someone Else",
    "description": "Stuff for sale",
    "chairs": 1,
    "tables": 0,
    "reception": True,
    "artShow": False,
    "charityRaffle": "Some stuff",
    "agreeToRules": True,
    "breakfast": True,
    "buttonOffer": "Buttons",
    "asstBreakfast": False,
}

TEST_DEALER_ASST_ARGS = {
    "name": "Foobian the First",
    "email": "dealer-assistant@mailinator.org",
    "license": "N/A",
}


class OrdersTestCase(TestCase):
    def setUp(self):
        self.price_free = PriceLevel(
            name="Free",
            description="I am Free!!",
            basePrice=0.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=False,
            isMinor=True,
            available_to_attendee=True,
        )
        self.price_minor_25 = PriceLevel(
            name="Minor",
            description="I am a Minor!",
            basePrice=25.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=False,
            isMinor=True,
            available_to_attendee=True,
        )
        self.price_accompanied_0 = PriceLevel(
            name="Accompanied",
            description="I am an Accompanied minor!",
            basePrice=0,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=False,
            isMinor=True,
            available_to_attendee=True,
        )
        self.price_minor_35 = PriceLevel(
            name="Minor",
            description="I am a public Minor!",
            basePrice=35.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=True,
            isMinor=True,
            available_to_attendee=True,
        )
        self.price_45 = PriceLevel(
            name="Attendee",
            description="Some test description here",
            basePrice=45.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=True,
            available_to_attendee=True,
        )
        self.price_90 = PriceLevel(
            name="Sponsor",
            description="Woot!",
            basePrice=90.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=True,
            available_to_attendee=True,
        )
        self.price_150 = PriceLevel(
            name="Super",
            description="In the future",
            basePrice=150.00,
            startDate=now + ten_days,
            endDate=now + ten_days + ten_days,
            public=True,
            available_to_attendee=True,
        )
        self.price_235 = PriceLevel(
            name="Elite",
            description="ooOOOoooooo",
            basePrice=235.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=False,
            available_to_attendee=True,
        )
        self.price_675 = PriceLevel(
            name="Raven God",
            description="yay",
            basePrice=675.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=False,
            emailVIP=True,
            emailVIPEmails="apis@mailinator.com",
            available_to_attendee=True,
        )
        self.price_free.save()
        self.price_minor_25.save()
        self.price_accompanied_0.save()
        self.price_minor_35.save()
        self.price_45.save()
        self.price_90.save()
        self.price_150.save()
        self.price_235.save()
        self.price_675.save()

        self.department1 = Department(name="BestDept")
        self.department2 = Department(name="WorstDept")
        self.department1.save()
        self.department2.save()

        self.discount = Discount(
            codeName="FiveOff", amountOff=5.00, startDate=now, endDate=now + ten_days
        )
        self.onetimediscount = Discount(
            codeName="OneTime",
            percentOff=10,
            oneTime=True,
            startDate=now,
            endDate=now + ten_days,
        )
        self.staffdiscount = Discount(
            codeName="StaffDiscount",
            amountOff=45.00,
            startDate=now,
            endDate=now + ten_days,
        )
        self.dealerdiscount = Discount(
            codeName="DealerDiscount",
            amountOff=45.00,
            startDate=now,
            endDate=now + ten_days,
        )
        self.discount.save()
        self.onetimediscount.save()
        self.staffdiscount.save()
        self.dealerdiscount.save()

        self.shirt1 = ShirtSizes(name="Test_Large")
        self.shirt1.save()

        self.option_conbook = PriceLevelOption.objects.create(
            optionName="Conbook", optionPrice=0.00, optionExtraType="bool"
        )
        self.option_shirt = PriceLevelOption.objects.create(
            optionName="Shirt Size", optionPrice=0.00, optionExtraType="ShirtSizes"
        )
        self.option_100_int = PriceLevelOption.objects.create(
            optionName="Something Pricy", optionPrice=100.00, optionExtraType="int"
        )
        self.option_pin = PriceLevelOption.objects.create(
            optionName="Pin", optionPrice=0, optionExtraType="bool", public=False
        )

        self.price_45.priceLevelOptions.add(self.option_conbook)
        self.price_45.priceLevelOptions.add(self.option_shirt)
        self.price_90.priceLevelOptions.add(self.option_conbook)
        self.price_90.priceLevelOptions.add(self.option_pin)
        self.price_150.priceLevelOptions.add(self.option_conbook)
        self.price_150.priceLevelOptions.add(self.option_100_int)
        self.price_150.priceLevelOptions.add(self.option_shirt)

        self.event = Event(**DEFAULT_EVENT_ARGS)
        self.event.staffDiscount = self.staffdiscount
        self.event.dealerDiscount = self.dealerdiscount
        self.event.save()

        self.table_130 = TableSize(
            name="Booth",
            description="description here",
            chairMin=0,
            chairMax=1,
            tableMin=0,
            tableMax=1,
            partnerMin=0,
            partnerMax=1,
            basePrice=Decimal(130),
        )
        self.table_160 = TableSize(
            name="Booth",
            description="description here",
            chairMin=0,
            chairMax=1,
            tableMin=0,
            tableMax=1,
            partnerMin=0,
            partnerMax=2,
            basePrice=Decimal(160),
        )

        self.table_130.save()
        self.table_160.save()

        self.attendee_form_1 = {
            "firstName": "Tester",
            "lastName": "Testerson",
            "address1": "123 Somewhere St",
            "address2": "",
            "city": "Place",
            "state": "PA",
            "country": "US",
            "postal": "12345",
            "phone": "1112223333",
            "email": "apis@mailinator.org",
            "birthdate": "1990-01-01",
            "asl": "false",
            "badgeName": "FluffyButtz",
            "emailsOk": "true",
            "volunteer": "false",
            "volDepts": "",
            "surveyOk": "false",
            "signature_svg": TEST_SIGNATURE_SVG,
        }
        self.attendee_form_2 = {
            "firstName": "Bea",
            "lastName": "Testerson",
            "address1": "123 Somewhere St",
            "address2": "Ste 300",
            "city": "Place",
            "state": "PA",
            "country": "US",
            "postal": "12345",
            "phone": "1112223333",
            "email": "apis@mailinator.com",
            "birthdate": "1990-01-01",
            "asl": "false",
            "badgeName": "FluffyButz",
            "emailsOk": "true",
            "volunteer": "false",
            "volDepts": "",
            "surveyOk": "false",
            "signature_svg": TEST_SIGNATURE_SVG,
        }

        self.attendee_form_upgrade = self.attendee_form_1
        self.attendee_form_upgrade["firstName"] = "Upgrade"
        self.attendee_form_upgrade["lastName"] = "Me"
        self.attendee_form_upgrade["badgeName"] = "Upgrade Test"

        self.attendee_form_upgrade_checkout = {
            "processor": "paypal",
            "billingData": {
                "address1": "Qui qui quasi amet",
                "address2": "Sunt voluptas dolori",
                "card_data": {
                    "billing_postal_code": "94044",
                    "card_brand": "VISA",
                    "digital_wallet_type": "NONE",
                    "exp_month": 12,
                    "exp_year": 2021,
                    "last_4": "1111",
                },
                "cc_firstname": "Whitney",
                "cc_lastname": "Thompson",
                "city": "Quam earum Nam dolor",
                "country": "FK",
                "email": "apis@mailinator.net",
                "source_id": "cnon:card-nonce-ok",
                "postal": "13271",
                "state": None,
            },
            "onsite": False,
            "orgDonation": "10",
        }

        self.attendee_upgrade = dict(
            firstName="Test",
            lastName="Upgrader",
            address1="123 Somewhere St",
            city="Place",
            state="PA",
            country="US",
            postalCode=12345,
            phone="1112223333",
            email="apis@mailinator.org",
            birthdate="1990-01-01",
        )

        self.client = Client()

    def add_to_cart(self, attendee, priceLevel, options):
        postData = {
            "attendee": attendee,
            "priceLevel": {"id": priceLevel.id, "options": options},
            "event": self.event.name,
        }

        response = self.client.post(
            reverse("registration:add_to_cart"),
            json.dumps(postData),
            content_type="application/json",
        )
        logging.info(response.content)
        self.assertEqual(response.status_code, 200)

    def zero_checkout(self):
        postData = {}
        response = self.client.post(
            reverse("registration:checkout"),
            json.dumps(postData),
            content_type="application/json",
            headers={"idempotency-key": str(uuid.uuid4())},
        )
        return response

    def get_paypal_checkout_postdata(
        self, code="", orgDonation="", charityDonation=""
    ) -> dict:
        postData = {
            "processor": "paypal",
            "billingData": {"source_id": "TEST-PAYPAL-ORDER"},
            "charityDonation": charityDonation,
            "onsite": False,
            "orgDonation": orgDonation,
        }

        if code:
            postData["paypalMockResponse"] = code

        return postData

    def get_square_checkout_postdata(
        self, token="", orgDonation="", charityDonation="", onsite=False
    ) -> dict:
        billingData = {}

        if not onsite:
            billingData = {
                "address1": "123 Any Street",
                "address2": "Apt 4",
                "cc_firstname": "Buffy",
                "cc_lastname": "Cleveland",
                "city": "39535",
                "country": "ST",
                "email": "apis@mailinator.net",
                "source_id": token,
                "postal": "45733",
                "state": "ID",
            }

        return {
            "processor": "square",
            "billingData": billingData,
            "charityDonation": charityDonation,
            "onsite": onsite,
            "orgDonation": orgDonation,
        }

    def checkout(
        self, token_or_code="", orgDonation="", charityDonation="", onsite=False
    ):
        postData = {}

        if token_or_code[:5] in ("cnon:", "ccof:", "bnon:", "wnon:"):
            postData = self.get_square_checkout_postdata(
                token_or_code, orgDonation, charityDonation, onsite
            )
        else:
            postData = self.get_paypal_checkout_postdata(
                token_or_code, orgDonation, charityDonation
            )

        response = self.client.post(
            reverse("registration:checkout"),
            json.dumps(postData),
            content_type="application/json",
            headers={"idempotency-key": str(uuid.uuid4())},
        )

        return response


# ---------------------------------------------------------------------------
# PayPal test fixtures / helper factories
# ---------------------------------------------------------------------------
#
# These helpers build plain-dict fixtures shaped like the PayPal v2 Orders API
# response that APIS stores on ``Order.apiData``. They intentionally do NOT
# import any ``paypalserversdk`` symbols so they can be used anywhere without
# requiring the PayPal SDK to be importable (e.g. status/enum strings are
# spelled out literally).
#
# The canonical shape replicated here mirrors the one produced by
# ``registration/paypal_payments.py`` for a single-purchase-unit,
# single-capture order (see ``test_paypal_payments.py:248-271`` for the
# reference ``order_no_refund`` shape). Variations actually observed in the
# existing tests that drove parameterization:
#
#   * ``payment_source.card.last_digits`` is present in the refresh tests but
#     absent from the refund/webhook tests -> controlled by ``last_four``.
#   * Top-level ``status`` field present in the webhook test only -> always
#     emitted with the sensible default ``"COMPLETED"`` so both shapes match.
#   * Outer ``purchase_units[0].amount`` present in the refund/webhook tests
#     but absent from the refresh tests -> always emitted for consistency
#     with what production actually writes (harmless when present).
#   * ``refunds`` array may be absent, contain one COMPLETED refund of
#     partial or full value, or contain multiple COMPLETED refunds summing
#     to partial or full value -> covered by the five convenience wrappers.


def make_refund_dict(
    id="TEST-PAYPAL-REFUND",
    amount="0.00",
    status="COMPLETED",
    note="",
    currency="USD",
):
    """Return a single PayPal refund dict in the ``currency_code``/``value``
    shape used in production.

    ``amount`` is coerced to a two-decimal string. ``status`` is a plain
    string (e.g. ``"COMPLETED"``, ``"PENDING"``, ``"FAILED"``) so callers
    don't need to import ``RefundStatus``.
    """
    refund = {
        "id": id,
        "status": status,
        "amount": {
            "currency_code": currency,
            "value": "%.2f" % Decimal(str(amount)),
        },
    }
    if note:
        refund["note_to_payer"] = note
    return refund


def make_paypal_apidata(
    *,
    order_id="TEST-PAYPAL-ORDER",
    capture_id="TEST-PAYPAL-CAPTURE",
    capture_status="COMPLETED",
    capture_amount="99.99",
    currency="USD",
    refunds=None,
    last_four=None,
):
    """Build an APIS ``Order.apiData`` dict shaped like a PayPal v2 Orders
    API response.

    Represents the single-purchase-unit, single-capture shape APIS always
    creates (see ``registration/paypal_payments.py:84-148``).

    Parameters
    ----------
    order_id:
        Top-level PayPal order id.
    capture_id:
        Capture id inside ``purchase_units[0].payments.captures[0]``.
    capture_status:
        Capture status string (e.g. ``"COMPLETED"``, ``"DECLINED"``,
        ``"PENDING"``). Passed through unchanged.
    capture_amount:
        Capture amount; coerced to a two-decimal string.
    currency:
        Currency code used for both the purchase unit amount and the
        capture amount.
    refunds:
        Either ``None`` (no ``refunds`` key emitted) or a list of refund
        dicts (as produced by :func:`make_refund_dict`).
    last_four:
        If given, a ``payment_source.card.last_digits`` field is added.
    """
    amount_value = "%.2f" % Decimal(str(capture_amount))
    data = {
        "id": order_id,
        "status": "COMPLETED",
        "purchase_units": [
            {
                "reference_id": "registration",
                "amount": {
                    "currency_code": currency,
                    "value": amount_value,
                },
                "payments": {
                    "captures": [
                        {
                            "id": capture_id,
                            "status": capture_status,
                            "amount": {
                                "currency_code": currency,
                                "value": amount_value,
                            },
                        }
                    ],
                },
            }
        ],
    }
    if last_four is not None:
        data["payment_source"] = {"card": {"last_digits": str(last_four)}}
    if refunds is not None:
        data["purchase_units"][0]["payments"]["refunds"] = list(refunds)
    return data


def paypal_apidata_no_refunds(**overrides):
    """PayPal apiData with no ``refunds`` key at all."""
    overrides.pop("refunds", None)
    return make_paypal_order_apidata(**overrides)


def paypal_apidata_one_partial_refund(amount, **overrides):
    """PayPal apiData with a single COMPLETED refund strictly less than the
    capture total.

    Raises ``ValueError`` if ``amount`` is not strictly less than
    ``capture_amount`` (default ``"99.99"``).
    """
    capture_amount = overrides.get("capture_amount", "99.99")
    if Decimal(str(amount)) >= Decimal(str(capture_amount)):
        raise ValueError(
            "partial refund amount %s must be < capture amount %s"
            % (amount, capture_amount)
        )
    refund_id = overrides.pop("refund_id", "TEST-PAYPAL-PARTIAL-REFUND")
    overrides["refunds"] = [make_refund_dict(id=refund_id, amount=amount)]
    return make_paypal_order_apidata(**overrides)


def paypal_apidata_fully_refunded(**overrides):
    """PayPal apiData with a single COMPLETED refund equal to the capture
    total."""
    capture_amount = overrides.get("capture_amount", "99.99")
    refund_id = overrides.pop("refund_id", "TEST-PAYPAL-FULL-REFUND")
    overrides["refunds"] = [make_refund_dict(id=refund_id, amount=capture_amount)]
    return make_paypal_order_apidata(**overrides)


def paypal_apidata_multi_partial(amounts, **overrides):
    """PayPal apiData with multiple COMPLETED refunds summing to strictly
    less than the capture total.

    ``amounts`` is an iterable of ``Decimal``/``float``/``str`` values.
    """
    capture_amount = Decimal(str(overrides.get("capture_amount", "99.99")))
    decs = [Decimal(str(a)) for a in amounts]
    if sum(decs) >= capture_amount:
        raise ValueError(
            "multi-partial refund amounts %s must sum to < capture amount %s"
            % (amounts, capture_amount)
        )
    overrides["refunds"] = [
        make_refund_dict(id="TEST-PAYPAL-PARTIAL-REFUND-%d" % i, amount=a)
        for i, a in enumerate(decs, start=1)
    ]
    return make_paypal_order_apidata(**overrides)


def paypal_apidata_multi_full(amounts, **overrides):
    """PayPal apiData with multiple COMPLETED refunds summing to exactly the
    capture total."""
    capture_amount = Decimal(str(overrides.get("capture_amount", "99.99")))
    decs = [Decimal(str(a)) for a in amounts]
    if sum(decs) != capture_amount:
        raise ValueError(
            "multi-full refund amounts %s must sum to == capture amount %s"
            % (amounts, capture_amount)
        )
    overrides["refunds"] = [
        make_refund_dict(id="TEST-PAYPAL-FULL-REFUND-%d" % i, amount=a)
        for i, a in enumerate(decs, start=1)
    ]
    return make_paypal_order_apidata(**overrides)


class PayPalOrdersTestCase(OrdersTestCase):
    """An :class:`OrdersTestCase` with a seeded COMPLETED PayPal-backed order.

    Creates:

    * ``self.order``      - an :class:`Order` in state ``COMPLETED`` with
      ``apiData`` shaped by :func:`paypal_apidata_no_refunds` and
      ``capture_id=PAYPAL_CAPTURE_ID``.
    * ``self.attendee``   - an :class:`Attendee` built from
      :data:`TEST_ATTENDEE_ARGS`.
    * ``self.badge``      - a :class:`Badge` tying the attendee to the event.
    * ``self.order_item`` - an :class:`OrderItem` linking the order and badge.

    The parent ``OrdersTestCase.setUp`` already creates the :class:`Event`
    and the full suite of :class:`PriceLevel` objects; this subclass reuses
    ``self.event`` rather than creating a second one.
    """

    PAYPAL_CAPTURE_ID = "3C679366HH908993F"
    PAYPAL_ORDER_ID = "5O190127TN364715T"
    PAYPAL_ORDER_TOTAL = "99.99"

    def setUp(self):
        super().setUp()
        self.order = Order(
            total=self.PAYPAL_ORDER_TOTAL,
            status=Order.COMPLETED,
            reference="FOOBAR",
            billingEmail="apis@mailinator.com",
            lastFour="1111",
            apiData=paypal_apidata_no_refunds(
                order_id=self.PAYPAL_ORDER_ID,
                capture_id=self.PAYPAL_CAPTURE_ID,
                capture_amount=self.PAYPAL_ORDER_TOTAL,
            ),
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
