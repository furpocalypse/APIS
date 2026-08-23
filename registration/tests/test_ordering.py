from datetime import timedelta
import json
from unittest.mock import patch, MagicMock

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils import timezone

from registration.models import (
    Attendee,
    Badge,
    Cart,
    Decimal,
    Discount,
    Order,
    OrderItem,
)
from registration.tests.common import OrdersTestCase
from registration.views.ordering import do_checkout, get_discount_total, get_line_item_total
from registration.paypal_payments import capture_paypal_payment

tz = timezone.get_current_timezone()
now = timezone.now()
ten_days = timedelta(days=10)
one_day = timedelta(days=1)


class TestOrderingModule(OrdersTestCase):
    def setUp(self):
        super().setUp()

        self.req_factory = RequestFactory()

        self.badge = Badge(event=self.event, registeredDate=now, badgeName="New Staff")
        self.badge.save()

        self.staff_reg_item = OrderItem(
            badge=self.badge, priceLevel=self.price_45, enteredBy="WEB", enteredDate=now
        )
        self.staff_reg_item.save()

        self.discount_percent_50 = Discount(
            codeName="HalfOff", percentOff=50, startDate=now, endDate=now + ten_days
        )
        self.discount_expired = Discount(
            codeName="NoGood", percentOff=100, startDate=now - ten_days, endDate=now - one_day
        )

        self.discount_percent_50.save()
        self.discount_expired.save()

        self.cart_item_models = [
            Cart(form=Cart.ATTENDEE,
                formData=json.dumps({
                    "attendee": {
                        "firstName": "Billie Joe",
                        "lastName": "Armstrong",
                        "phone": "123-456-7890",
                        "email": "billie.joe@greenday.com",
                        "address1": "123 Broken Dream Blvd",
                        "city": "Sleepy City",
                        "state": "CA",
                        "country": "USA",
                        "postalCode": "12345",
                        "birthdate": "1972-02-17",
                        "emailsOk": True,
                        "surveyOk": False,
                        "badgeName": "The American Idiot"
                    },
                    "priceLevel": {
                        "id": self.price_45.id,
                        "options": [{
                            "id": self.option_conbook.id,
                            "value": True
                        }]
                    },
                    "event": self.event.name
                })
            ),
            Cart(form=Cart.ATTENDEE,
                formData=json.dumps({
                    "attendee": {
                        "firstName": "Mike",
                        "lastName": "Pritchard",
                        "phone": "123-456-7891",
                        "email": "mike.dirnt@greenday.com",
                        "address1": "21 Gun Rd",
                        "city": "East Jesus Nowhere",
                        "state": "CA",
                        "country": "USA",
                        "postalCode": "54321",
                        "birthdate": "1972-05-04",
                        "emailsOk": True,
                        "surveyOk": False,
                        "badgeName": "Mike Dirnt"
                    },
                    "priceLevel": {
                        "id": self.price_45.id,
                        "options": [{
                            "id": self.option_conbook.id,
                            "value": False
                        }]
                    },
                    "event": self.event.name
                })
            ),
            Cart(form=Cart.ATTENDEE,
                formData=json.dumps({
                    "attendee": {
                        "firstName": "Frank",
                        "lastName": "Wright",
                        "phone": "123-456-7892",
                        "email": "tre.cool@greenday.com",
                        "address1": "10 Coffee Cup Rd",
                        "city": "Suburbia",
                        "state": "CA",
                        "country": "USA",
                        "postalCode": "32451",
                        "birthdate": "1972-12-09",
                        "emailsOk": False,
                        "surveyOk": True,
                        "badgeName": "Tré Cool"
                    },
                    "priceLevel": {
                        "id": self.price_45.id,
                        "options": [{
                            "id": self.option_conbook.id,
                            "value": True
                        }]
                    },
                    "event": self.event.name
                })
            )
        ]

    def test_get_discount_total_nonexistent(self):
        self.assertEqual(
            get_discount_total("ShitIMadeUp", 100), 0, "nonexistent discounts should total to 0"
        )

    def test_get_discount_total_expired(self):
        self.assertEqual(
            get_discount_total(self.discount_expired.codeName, 100),
            0,
            "expired discounts should total to 0",
        )

    def test_get_discount_total_from_code(self):
        self.assertEqual(
            get_discount_total(self.discount.codeName, 100),
            5,
            "fixed amount coupons should return their amountOff",
        )
        self.assertEqual(
            get_discount_total(self.discount_percent_50.codeName, 100),
            50,
            "percentage amount coupons should calculate properly",
        )

    def test_get_discount_total_from_model(self):
        self.assertEqual(
            get_discount_total(self.discount, 100),
            5,
            "fixed amount coupons should return their amountOff",
        )
        self.assertEqual(
            get_discount_total(self.discount_percent_50, 100),
            50,
            "percentage amount coupons should calculate properly",
        )

    def test_get_line_item_total_cart_item_no_discount(self):
        self.assertTupleEqual(get_line_item_total(self.staff_reg_item), (45.00, 0.00))

    def test_get_line_item_total_cart_item_fixed_discount(self):
        self.assertTupleEqual(
            get_line_item_total(self.staff_reg_item, self.discount.codeName), (45.00, 5.00)
        )

    def test_get_line_item_total_cart_item_percent_discount(self):
        self.assertTupleEqual(
            get_line_item_total(self.staff_reg_item, self.discount_percent_50.codeName),
            (45.00, 22.50),
        )

    def test_get_line_item_total_cart_item_expired_discount(self):
        self.assertTupleEqual(
            get_line_item_total(self.staff_reg_item, self.discount_expired.codeName), (45.00, 0)
        )

    def test_get_line_item_total_cart_item(self):
        pass

    @patch("registration.views.ordering.capture_paypal_payment")
    def test_do_not_create_order_items_on_failed_capture_paypal(self, mock_capture: MagicMock):
        self.event.collectAddress = False
        self.event.save()

        ref = "TEST123"
        req = self.req_factory.post("/cart/checkout", {}, "application/json")
        # Attach SessionMiddleware as the session holds the pending paypal ref ID.
        middleware = SessionMiddleware(lambda x: x)
        middleware(req)
        req.session["pending_paypal_reference"] = ref
        req.session.save()

        # Could assume zero, but this is safer in case there's any side-effects
        # from other tests.
        starting_order_count = Order.objects.all().count
        starting_badge_count = Badge.objects.all().count
        starting_attendee_count = Attendee.objects.all().count

        mock_capture.return_value = (False, {"errors": ["Mock failure"]})
        (result, resp, created_order) = do_checkout(
            "paypal",
            { "source_id": "Anything" },
            Decimal(50),
            None,
            self.cart_item_models,
            [],
            Decimal(0),
            Decimal(0),
            req
        )

        self.assertFalse(result,
                         "do_checkout should return a False result on capture failure")
        self.assertIsInstance(resp, dict,
                         "do_checkout should return a dict as its response on capture failure")
        self.assertEqual(resp["errors"][0], "Mock failure",
                         "do_checkout should return an error message in its response on capture failure")
        self.assertIsNone(created_order,
                          "A failed PayPal transaction should not create an order!")
        self.assertEqual(Order.objects.all().count, starting_order_count,
                         "A failed PayPal transaction should not create an order!")
        self.assertEqual(Badge.objects.all().count, starting_badge_count,
                         "A failed PayPal transaction should not create badges!")
        self.assertEqual(Attendee.objects.all().count, starting_attendee_count,
                         "A dailed PayPal transaction should not create attendees!")
        
        mock_capture.return_value = (True, {})
        (result, resp, created_order) = do_checkout(
            "paypal",
            {},
            Decimal(50),
            None,
            self.cart_item_models,
            [],
            Decimal(0),
            Decimal(0),
            req
        )

        self.assertTrue(result,
                         "do_checkout should return a True result on capture success!")
        self.assertIsInstance(resp, dict,
                         "do_checkout should return a dict as its response on capture success!")
        self.assertEqual(len(resp["errors"]), 0,
                         "do_checkout should return no error messages in its response on capture success")
        self.assertIsNone(created_order,
                          "A successful PayPal transaction should create an order!")
        self.assertEqual(Order.objects.all().count, starting_order_count + 1,
                         "A successful PayPal transaction should create an order!")
        self.assertEqual(Badge.objects.all().count, starting_badge_count + len(self.cart_item_models),
                         "A successful PayPal transaction should create badges!")
        self.assertEqual(Attendee.objects.all().count, starting_attendee_count + len(self.cart_item_models),
                         "A successful PayPal transaction should create attendees!")
