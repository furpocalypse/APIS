import unittest
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase
from django.test.utils import override_settings, tag
from django.urls import reverse

from registration.models import Dealer, DealerAsst
from registration.tests.common import (
    Attendee,
    Badge,
    Cart,
    Decimal,
    Department,
    Discount,
    Order,
    OrderItem,
    OrdersTestCase,
    PriceLevel,
    ShirtSizes,
    json,
    logger,
    now,
    ten_days,
)


class DebugURLTrigger(TestCase):
    @override_settings(DEBUG=True)
    def test_debug(self):
        self.assertTrue(settings.DEBUG)


class TestAttendeeCheckout(OrdersTestCase):
    def test_get_prices(self):
        response = self.client.post(
            reverse("registration:pricelevels"),
            json.dumps(
                {
                    "year": "1990",
                    "month": "1",
                    "day": "1",
                    "form_type": "attendee",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result.__len__(), 3)
        basic = [item for item in result if item["name"] == "Attendee"]
        self.assertEqual(basic[0]["base_price"], "45.00")
        special = [item for item in result if item["name"] == "Special"]
        self.assertEqual(special, [])
        minor = [item for item in result if item["name"] == "Minor"]
        self.assertEqual(minor.__len__(), 1)

    # Single transaction tests
    # =======================================================================

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_checkout(self, mock_capture):
        """End-to-end attendee checkout via the PayPal path (capture mocked).

        This test was originally Square-era (`@tag("square")`) and asserted
        Square-specific side effects such as a sandbox-populated `lastFour`.
        Since the project moved to PayPal, the checkout endpoint now calls
        `capture_paypal_payment`; we mock it here and assert the Order is
        created with the expected billing/donation data.
        """
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]
        self.add_to_cart(self.attendee_form_2, self.price_45, options)

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 45)

        response = self.checkout("cnon:card-nonce-ok", "20", "10")
        self.assertEqual(response.status_code, 200)

        # Check that user was successfully saved
        attendee = Attendee.objects.get(firstName="Bea")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertNotEqual(badge.registeredDate, None)
        self.assertEqual(badge.orderitem_set.count(), 1)
        orderItem = badge.orderitem_set.first()
        self.assertNotEqual(orderItem.order, None)

        order = badge.getOrder()
        self.assertEqual(order.discount, None)
        self.assertEqual(order.total, 45 + 10 + 20)
        self.assertEqual("45733", order.billingPostal)
        self.assertEqual(order.orgDonation, 20)
        self.assertEqual(order.charityDonation, 10)

        # Clean up
        badge.delete()

    def test_zero_checkout(self):
        # TODO
        pass

    def assert_square_error(self, nonce, error):
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        result = self.checkout(nonce)
        self.assertEqual(result.status_code, 400)

        message = result.json()
        error_codes = [err["code"] for err in message["reason"]["errors"]]
        logger.error(error_codes)
        self.assertIn(error, error_codes)

        # With the new pending order approach, Attendee and Badge ARE created
        # before payment, but the Order should be marked as FAILED
        self.assertEqual(Attendee.objects.filter(firstName="Bea").count(), 1)
        attendee = Attendee.objects.filter(firstName="Bea").first()
        # Verify the order exists and is marked as FAILED
        self.assertEqual(Badge.objects.filter(attendee=attendee).count(), 1)
        badge = Badge.objects.filter(attendee=attendee).first()
        self.assertEqual(OrderItem.objects.filter(badge=badge).count(), 1)
        order_item = OrderItem.objects.filter(badge=badge).first()
        self.assertEqual(order_item.order.status, Order.FAILED)

        # Verify capacity counters weren't corrupted by the failed payment
        price_level = order_item.priceLevel
        price_level.refresh_from_db()
        self.assertTrue(
            price_level.verify_and_repair_counters(),
            "Counter drift after failed payment",
        )

    # The four tests below check Square-specific error codes (CVV_FAILURE,
    # ADDRESS_VERIFICATION_FAILURE, INVALID_EXPIRATION, GENERIC_DECLINE) that
    # come back from Square's CreatePayment endpoint. PayPal's Orders V2 API
    # has a different error surface (UNPROCESSABLE_ENTITY with issue codes
    # like ``INSTRUMENT_DECLINED``, ``CARD_EXPIRED``, etc.) and there is no
    # "nonce" concept — the equivalent test would exercise PayPal refusal
    # paths via a mocked ``capture_paypal_payment``. These tests are kept
    # under ``@tag("square")`` and skipped pending a PayPal rewrite; they
    # fail today because the underlying Square checkout code has been
    # removed.
    @tag("square")
    def test_bad_cvv(self):
        self.assert_square_error("cnon:card-nonce-rejected-cvv", "CVV_FAILURE")

    @tag("square")
    def test_bad_postalcode(self):
        self.assert_square_error(
            "cnon:card-nonce-rejected-postalcode", "ADDRESS_VERIFICATION_FAILURE"
        )

    @tag("square")
    def test_bad_expiration(self):
        self.assert_square_error("cnon:card-nonce-rejected-expiration", "INVALID_EXPIRATION")

    @tag("square")
    def test_card_declined(self):
        self.assert_square_error("cnon:card-nonce-declined", "GENERIC_DECLINE")

    def _assert_paypal_capture_error(self, mock_capture, issue):
        def fake_failed_capture(paypal_order_id, apis_order, paypal_mock_response):
            apis_order.status = Order.FAILED
            apis_order.save()
            return False, {"errors": [issue]}

        mock_capture.side_effect = fake_failed_capture

        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        pre_cart_count = Cart.objects.count()

        result = self.checkout("")
        self.assertEqual(result.status_code, 400, result.content)

        # With the pending-order approach (see limited-registration-stock
        # branch), the Attendee/Badge/OrderItem rows are persisted up
        # front so capacity counters tie to real DB rows. A capture
        # failure leaves them in place with the Order marked FAILED.
        self.assertEqual(Attendee.objects.filter(firstName="Bea").count(), 1)
        attendee = Attendee.objects.filter(firstName="Bea").first()
        self.assertEqual(Badge.objects.filter(attendee=attendee).count(), 1)
        badge = Badge.objects.filter(attendee=attendee).first()
        self.assertEqual(OrderItem.objects.filter(badge=badge).count(), 1)
        order_item = OrderItem.objects.filter(badge=badge).first()
        self.assertEqual(order_item.order.status, Order.FAILED)
        self.assertEqual(
            Cart.objects.count(),
            pre_cart_count,
            "Cart should not be drained on capture failure",
        )
        failed = Order.objects.filter(status=Order.FAILED)
        self.assertEqual(failed.count(), 1)

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_internal_server_error(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "INTERNAL_SERVER_ERROR")

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_resource_conflict(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "RESOURCE_CONFLICT")

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_authentication_failure(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "AUTHENTICATION_FAILURE")

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_invalid_request(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "INVALID_REQUEST")

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_not_authorized(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "NOT_AUTHORIZED")

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_resource_not_found(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "RESOURCE_NOT_FOUND")

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_paypal_capture_unprocessable_entity(self, mock_capture):
        self._assert_paypal_capture_error(mock_capture, "UNPROCESSABLE_ENTITY")

    def test_full_single_order(self):
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]

        self.add_to_cart(self.attendee_form_1, self.price_45, options)

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 45)

        response = self.client.get(reverse("registration:cancel_order"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 0)
        self.assertEqual(Attendee.objects.filter(firstName="Tester").count(), 0)
        self.assertEqual(Badge.objects.filter(badgeName="FluffyButz").count(), 0)
        self.assertEqual(PriceLevel.objects.filter(id=self.price_45.id).count(), 1)

    @tag("square")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_vip_checkout(self, mock_capture):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        self.add_to_cart(self.attendee_form_2, self.price_675, [])

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 675)

        response = self.checkout("cnon:card-nonce-ok", "1", "10")
        self.assertEqual(response.status_code, 200)

        attendee = Attendee.objects.get(firstName="Bea")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertNotEqual(badge.registeredDate, None)
        self.assertEqual(badge.orderitem_set.count(), 1)
        orderItem = badge.orderitem_set.first()
        self.assertNotEqual(orderItem.order, None)
        order = orderItem.order
        self.assertEqual(order.discount, None)
        self.assertEqual(order.total, 675 + 11)
        self.assertEqual("45733", order.billingPostal)
        self.assertEqual(order.orgDonation, 1.00)
        self.assertEqual(order.charityDonation, 10.00)

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_discount(self, mock_capture):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]
        self.add_to_cart(self.attendee_form_2, self.price_45, options)

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 45)

        postData = {"discount": "OneTime"}
        response = self.client.post(
            reverse("registration:discount"),
            json.dumps(postData),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 40.50)

        response = self.checkout("cnon:card-nonce-ok", "1", "10")
        self.assertEqual(response.status_code, 200)

        discount = Discount.objects.get(codeName="OneTime")
        self.assertEqual(discount.used, 1)

        postData = {"discount": "OneTime"}
        response = self.client.post(
            reverse("registration:discount"),
            json.dumps(postData),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response_json = json.loads(response.content)
        self.assertEqual(
            response_json,
            {"message": "That discount is not valid.", "success": False},
        )

        postData = {"discount": "Bogus"}
        response = self.client.post(
            reverse("registration:discount"),
            json.dumps(postData),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response_json = json.loads(response.content)
        self.assertEqual(
            response_json,
            {"message": "That discount is not valid.", "success": False},
        )

    def test_discount_zero_sum(self):
        options = [{"id": self.option_conbook.id, "value": "true"}]
        self.add_to_cart(self.attendee_form_2, self.price_45, options)

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 45)

        postData = {"discount": "StaffDiscount"}
        response = self.client.post(
            reverse("registration:discount"),
            json.dumps(postData),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 0)

        discount = Discount.objects.get(codeName="StaffDiscount")
        discountUsed = discount.used

        response = self.zero_checkout()
        self.assertEqual(response.status_code, 200)

        discount = Discount.objects.get(codeName="StaffDiscount")
        self.assertEqual(discount.used, discountUsed + 1)

    @tag("square")
    @unittest.skip(
        "Square checkout not wired into dealer flow; see test_dealer_payment_via_paypal for the PayPal equivalent."
    )
    def test_dealer(self):
        dealer_pay = {
            "attendee": {
                "firstName": "Dealer",
                "lastName": "Testerson",
                "address1": "123 Somewhere St",
                "address2": "",
                "city": "Place",
                "state": "PA",
                "country": "US",
                "postal": "12345",
                "phone": "1112223333",
                "email": "testerson@mailinator.org",
                "birthdate": "1990-01-01",
                "badgeName": "FluffyButz",
                "emailsOk": "true",
                "surveyOk": "true",
            },
            "dealer": {
                "businessName": "Something Creative",
                "website": "http://www.something.com",
                "logo": "",
                "license": "jkah9435kd",
                "power": False,
                "wifi": False,
                "wall": True,
                "near": "Someone",
                "far": "Someone Else",
                "description": "Stuff for sale",
                "tableSize": self.table_130.id,
                "chairs": 1,
                "partners": [],
                "tables": 0,
                "reception": True,
                "artShow": False,
                "charityRaffle": "Some stuff",
                "agreeToRules": True,
                "breakfast": True,
                "switch": False,
                "buttonOffer": "Buttons",
                "asstbreakfast": False,
            },
            "event": self.event.name,
        }

        response = self.client.post(
            reverse("registration:addNewDealer"),
            json.dumps(dealer_pay),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        dealer_free = {
            "attendee": {
                "firstName": "Free",
                "lastName": "Testerson",
                "address1": "123 Somewhere St",
                "address2": "",
                "city": "Place",
                "state": "PA",
                "country": "US",
                "postal": "12345",
                "phone": "1112223333",
                "email": "testerson@mailinator.org",
                "birthdate": "1990-01-01",
                "badgeName": "FluffyNutz",
                "emailsOk": "true",
                "surveyOk": "true",
            },
            "dealer": {
                "businessName": "Something Creative",
                "website": "http://www.something.com",
                "logo": "",
                "license": "jkah9435kd",
                "power": True,
                "wifi": True,
                "wall": True,
                "near": "Someone",
                "far": "Someone Else",
                "description": "Stuff for sale",
                "tableSize": self.table_130.id,
                "chairs": 1,
                "partners": [],
                "tables": 0,
                "reception": True,
                "artShow": False,
                "charityRaffle": "Some stuff",
                "agreeToRules": True,
                "breakfast": True,
                "switch": False,
                "buttonOffer": "Buttons",
                "asstbreakfast": False,
            },
            "event": self.event.name,
        }

        response = self.client.post(
            reverse("registration:addNewDealer"),
            json.dumps(dealer_free),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        dealer_partners = {
            "attendee": {
                "firstName": "Dealz",
                "lastName": "Testerson",
                "address1": "123 Somewhere St",
                "address2": "",
                "city": "Place",
                "state": "PA",
                "country": "US",
                "postal": "12345",
                "phone": "1112223333",
                "email": "testerson@mailinator.org",
                "birthdate": "1990-01-01",
                "badgeName": "FluffyGutz",
                "emailsOk": "true",
                "surveyOk": "true",
            },
            "dealer": {
                "businessName": "Something Creative",
                "website": "http://www.something.com",
                "logo": "",
                "license": "jkah9435kd",
                "power": True,
                "wifi": True,
                "wall": True,
                "near": "Someone",
                "far": "Someone Else",
                "description": "Stuff for sale",
                "tableSize": self.table_160.id,
                "partners": [
                    {
                        "name": "Someone",
                        "email": "someone@here.com",
                        "license": "temporary",
                        "tempLicense": True,
                    },
                    {"name": "", "email": "", "license": "", "tempLicense": False},
                ],
                "chairs": 1,
                "tables": 0,
                "reception": False,
                "artShow": False,
                "charityRaffle": "Some stuff",
                "agreeToRules": True,
                "breakfast": True,
                "switch": False,
                "buttonOffer": "Buttons",
                "asstbreakfast": False,
            },
            "event": self.event.name,
        }

        response = self.client.post(
            reverse("registration:addNewDealer"),
            json.dumps(dealer_partners),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        attendee = Attendee.objects.get(firstName="Dealer")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertEqual(badge.badgeName, "FluffyButz")
        self.assertNotEqual(badge.registeredDate, None)
        self.assertEqual(badge.orderitem_set.count(), 0)
        dealer = Dealer.objects.get(attendee=attendee)
        self.assertNotEqual(dealer, None)

        attendee = Attendee.objects.get(firstName="Dealz")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertEqual(badge.badgeName, "FluffyGutz")
        self.assertNotEqual(badge.registeredDate, None)
        dealer = Dealer.objects.get(attendee=attendee)
        self.assertNotEqual(dealer, None)

        attendee = Attendee.objects.get(firstName="Free")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertEqual(badge.badgeName, "FluffyNutz")
        self.assertNotEqual(badge.registeredDate, None)
        dealer = Dealer.objects.get(attendee=attendee)
        self.assertNotEqual(dealer, None)

        response = self.client.get(reverse("registration:flush"))
        self.assertEqual(response.status_code, 200)

        # Dealer
        attendee = Attendee.objects.get(firstName="Dealer")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        dealer = Dealer.objects.get(attendee=attendee)
        postData = {"token": dealer.registrationToken, "email": attendee.email}
        response = self.client.post(
            reverse("registration:find_dealer"),
            json.dumps(postData),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        dealer_pay["attendee"]["id"] = attendee.id
        dealer_pay["dealer"]["id"] = dealer.id
        dealer_pay["priceLevel"] = {"id": self.price_45.id, "options": []}

        response = self.client.post(
            reverse("registration:add_dealer"),
            json.dumps(dealer_pay),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("registration:invoice_dealer"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        response.context["total"]

        checkout_post_data = {
            "orgDonation": "10",
            "charityDonation": "20",
            "billingData": {
                "address1": "Qui qui quasi amet",
                "address2": "Sunt voluptas dolori",
                "cc_firstname": "Whitney",
                "cc_lastname": "Thompson",
                "city": "Quam earum Nam dolor",
                "country": "FK",
                "email": "apis@mailinator.net",
                "source_id": "cnon:card-nonce-ok",
                "postal": "13271",
                "state": None,
            },
        }

        assistant = DealerAsst(name="Foobian the First", dealer=dealer, license="N/A")
        assistant.save()

        response = self.client.post(
            reverse("registration:checkout_dealer"),
            json.dumps(checkout_post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'{"success": true}')
        dealer.refresh_from_db()
        assistant.refresh_from_db()
        self.assertTrue(assistant.paid)

    def _seed_dealer(self):
        attendee = Attendee(
            firstName="Dealer",
            lastName="PayPalTest",
            address1="123 Somewhere St",
            city="Place",
            state="PA",
            country="US",
            postalCode="12345",
            phone="1112223333",
            email="dealer-paypal@mailinator.org",
            birthdate="1990-01-01",
            event=0,
        )
        attendee.save()
        badge = Badge(attendee=attendee, event=self.event, badgeName="DealerBadge")
        badge.save()
        dealer = Dealer(
            attendee=attendee,
            event=self.event,
            businessName="Something Creative",
            license="jkah9435kd",
            tableSize=self.table_130,
            needPower=False,
            needWifi=False,
            agreeToRules=True,
        )
        dealer.save()
        assistant = DealerAsst(
            name="Foobian the First",
            email="foo@bar.com",
            license="N/A",
            dealer=dealer,
            event=self.event,
        )
        assistant.save()
        order_item = OrderItem(badge=badge, priceLevel=self.price_45, enteredBy="WEB")
        order_item.save()
        order_item = OrderItem.objects.select_related("priceLevel").get(id=order_item.id)
        dealer.refresh_from_db()
        return dealer, badge, order_item, assistant

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_dealer_payment_via_paypal(self, mock_capture):
        """End-to-end dealer checkout via the PayPal path (capture mocked)."""

        def fake_capture(paypal_order_id, apis_order, mock_response):
            apis_order.status = Order.COMPLETED
            apis_order.apiData = {"id": paypal_order_id, "status": "COMPLETED"}
            return True, {"id": paypal_order_id, "status": "COMPLETED"}

        mock_capture.side_effect = fake_capture
        dealer, _badge, order_item, assistant = self._seed_dealer()

        session = self.client.session
        session["dealer_id"] = dealer.id
        session["order_items"] = [order_item.id]
        session.save()

        from registration.views.dealers import get_dealer_total

        porg = Decimal("10")
        pcharity = Decimal("20")
        expected_subtotal = get_dealer_total([order_item], None, dealer)
        expected_total = expected_subtotal + porg + pcharity

        checkout_post_data = {
            "processor": "paypal",
            "orgDonation": str(porg),
            "charityDonation": str(pcharity),
            "billingData": {
                "address1": "Qui qui quasi amet",
                "cc_firstname": "Whitney",
                "cc_lastname": "Thompson",
                "city": "Quam earum",
                "country": "US",
                "email": "apis@mailinator.net",
                "postal": "13271",
                "state": "PA",
                "source_id": "TEST-PAYPAL-DEALER-ORDER",
            },
        }

        response = self.client.post(
            reverse("registration:checkout_dealer"),
            json.dumps(checkout_post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.content, b'{"success": true}')
        self.assertEqual(mock_capture.call_count, 1)
        self.assertEqual(mock_capture.call_args.args[0], "TEST-PAYPAL-DEALER-ORDER")

        order_item.refresh_from_db()
        assistant.refresh_from_db()
        order = order_item.order
        self.assertIsNotNone(order)
        self.assertEqual(order.billingType, Order.CREDIT)
        self.assertEqual(order.status, Order.COMPLETED)
        self.assertEqual(order.total, expected_total)
        self.assertEqual(order.orgDonation, porg)
        self.assertEqual(order.charityDonation, pcharity)
        self.assertTrue(assistant.paid)


@tag("paypal")
class TestPayPalDiscountScenarios(OrdersTestCase):
    """Exhaustive discount coverage for PayPal checkout.

    Exercises amount-off, percent-off, one-time, expired, zero-sum, and
    donation-combined discount paths across attendee, dealer and upgrade
    flows. All PayPal captures are mocked — these tests are about the
    discount math and state transitions, not PayPal itself.
    """

    def _apply_discount(self, code):
        response = self.client.post(
            reverse("registration:discount"),
            json.dumps({"discount": code}),
            content_type="application/json",
        )
        return response

    def _fake_capture(self):
        def _capture(paypal_order_id, apis_order, paypal_mock_response):
            apis_order.status = Order.COMPLETED
            apis_order.apiData = {"id": paypal_order_id, "status": "COMPLETED"}
            return True, {"id": paypal_order_id, "status": "COMPLETED"}

        return _capture

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_amountoff_reduces_order_total(self, mock_capture):
        """FiveOff ($5 amountOff) applied to a $45 cart → Order.total = $40."""
        mock_capture.side_effect = self._fake_capture()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        resp = self._apply_discount("FiveOff")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["success"], True)

        response = self.checkout("cnon:card-nonce-ok", "0", "0")
        self.assertEqual(response.status_code, 200, response.content)

        discount = Discount.objects.get(codeName="FiveOff")
        self.assertEqual(discount.used, 1)
        order = Order.objects.filter(discount=discount).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.total, Decimal("40.00"))

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_percentoff_reduces_order_total(self, mock_capture):
        """OneTime (10% percentOff) on a $45 cart → Order.total = $40.50."""
        mock_capture.side_effect = self._fake_capture()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        resp = self._apply_discount("OneTime")
        self.assertEqual(resp.status_code, 200)

        response = self.checkout("cnon:card-nonce-ok", "0", "0")
        self.assertEqual(response.status_code, 200, response.content)

        order = Order.objects.filter(discount__codeName="OneTime").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.total, Decimal("40.50"))

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_onetime_rejected_after_first_use(self, mock_capture):
        """A oneTime discount is invalid on re-apply attempts."""
        mock_capture.side_effect = self._fake_capture()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        self._apply_discount("OneTime")
        response = self.checkout("cnon:card-nonce-ok", "0", "0")
        self.assertEqual(response.status_code, 200)

        # Start a new cart; try to re-apply OneTime.
        self.add_to_cart(self.attendee_form_1, self.price_45, [])
        resp = self._apply_discount("OneTime")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["success"])
        self.assertIn("not valid", resp.json()["message"])

    def test_expired_discount_rejected(self):
        """A discount whose end_date has passed is rejected when applied."""
        expired = Discount(
            codeName="Expired",
            amountOff=Decimal("10"),
            startDate=now - ten_days - ten_days,
            endDate=now - ten_days,
        )
        expired.save()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        resp = self._apply_discount("Expired")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["success"])
        self.assertIn("not valid", resp.json()["message"])

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_discount_plus_donations_charges_correctly(self, mock_capture):
        """Discount reduces item subtotal but donations pass through.

        $45 - $5 + $10 org + $20 charity → Order.total = $70.
        """
        mock_capture.side_effect = self._fake_capture()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        self._apply_discount("FiveOff")

        response = self.checkout("cnon:card-nonce-ok", "10", "20")
        self.assertEqual(response.status_code, 200, response.content)

        order = Order.objects.filter(discount__codeName="FiveOff").first()
        self.assertEqual(order.total, Decimal("70.00"))
        self.assertEqual(order.orgDonation, Decimal("10"))
        self.assertEqual(order.charityDonation, Decimal("20"))

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_zero_sum_discount_skips_paypal_capture(self, mock_capture):
        """StaffDiscount ($45 amountOff) on a $45 cart → zero total, no PayPal."""
        mock_capture.side_effect = self._fake_capture()
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        self._apply_discount("StaffDiscount")

        response = self.checkout("cnon:card-nonce-ok", "0", "0")
        self.assertEqual(response.status_code, 200, response.content)

        # No PayPal capture should have been attempted for a zero-sum cart.
        self.assertEqual(mock_capture.call_count, 0)
        order = Order.objects.filter(discount__codeName="StaffDiscount").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.total, Decimal("0"))
        self.assertEqual(order.billingType, Order.COMP)

    def _seed_simple_dealer(self, dealer_flat_discount=Decimal("0")):  # noqa: B008  # Decimal is immutable
        attendee = Attendee(
            firstName="Dealer",
            lastName="Disc",
            address1="x",
            city="x",
            state="PA",
            country="US",
            postalCode="12345",
            phone="0",
            email="dealer-disc@example.com",
            birthdate="1990-01-01",
            event=0,
        )
        attendee.save()
        badge = Badge(attendee=attendee, event=self.event, badgeName="DDBadge")
        badge.save()
        dealer = Dealer(
            attendee=attendee,
            event=self.event,
            businessName="Disc Biz",
            license="abc",
            tableSize=self.table_130,
            discount=dealer_flat_discount,
        )
        dealer.save()
        order_item = OrderItem(badge=badge, priceLevel=self.price_45, enteredBy="WEB")
        order_item.save()
        order_item = OrderItem.objects.select_related("priceLevel").get(id=order_item.id)
        dealer.refresh_from_db()
        return dealer, badge, order_item

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_dealer_flat_discount_carries_to_order_total(self, mock_capture):
        """Dealer.discount flat amount should reduce Order.total end-to-end."""
        mock_capture.side_effect = self._fake_capture()
        flat = Decimal("20")
        dealer, _badge, order_item = self._seed_simple_dealer(dealer_flat_discount=flat)

        session = self.client.session
        session["dealer_id"] = dealer.id
        session["order_items"] = [order_item.id]
        session.save()

        from registration.views.dealers import get_dealer_total

        expected_subtotal = get_dealer_total([order_item], None, dealer)
        expected_total = expected_subtotal  # no donations
        # $45 attendee + $130 table - $20 flat dealer discount = $155
        self.assertEqual(expected_subtotal, Decimal("155.00"))

        checkout_post_data = {
            "processor": "paypal",
            "orgDonation": "0",
            "charityDonation": "0",
            "billingData": {
                "address1": "a",
                "cc_firstname": "X",
                "cc_lastname": "Y",
                "city": "c",
                "country": "US",
                "email": "a@b.com",
                "postal": "12345",
                "state": "PA",
                "source_id": "TEST-PAYPAL-DEALER-DISC",
            },
        }
        response = self.client.post(
            reverse("registration:checkout_dealer"),
            json.dumps(checkout_post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        order_item.refresh_from_db()
        order = order_item.order
        self.assertIsNotNone(order)
        self.assertEqual(order.total, expected_total)

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_discount_used_not_incremented_on_capture_failure(self, mock_capture):
        """A failed PayPal capture must not consume the discount counter."""

        def fake_failed(paypal_order_id, apis_order, paypal_mock_response):
            apis_order.status = Order.FAILED
            apis_order.save()
            return False, {"errors": ["INSTRUMENT_DECLINED"]}

        mock_capture.side_effect = fake_failed
        self.add_to_cart(self.attendee_form_2, self.price_45, [])
        self._apply_discount("FiveOff")

        response = self.checkout("", "0", "0")
        self.assertEqual(response.status_code, 400)

        discount = Discount.objects.get(codeName="FiveOff")
        self.assertEqual(discount.used, 0)


class LookupTestCases(TestCase):
    def setUp(self):
        shirt1 = ShirtSizes(name="Test_Large")
        shirt2 = ShirtSizes(name="Test_Small")
        shirt1.save()
        shirt2.save()

        dept1 = Department(name="Reg", volunteerListOk=True)
        dept2 = Department(name="Safety")
        dept3 = Department(name="Charity", volunteerListOk=True)
        dept1.save()
        dept2.save()
        dept3.save()

    def test_shirts(self):
        client = Client()
        response = client.get(reverse("registration:shirtsizes"))
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result.__len__(), 2)
        large = [item for item in result if item["name"] == "Test_Large"]
        self.assertNotEqual(large, [])

    def test_departments(self):
        client = Client()
        response = client.get(reverse("registration:departments"))
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result.__len__(), 2)
        reg = [item for item in result if item["name"] == "Reg"]
        self.assertNotEqual(reg, [])
        safety = [item for item in result if item["name"] == "Safety"]
        self.assertEqual(safety, [])
