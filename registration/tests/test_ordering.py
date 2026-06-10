from datetime import timedelta

from django.utils import timezone

from registration.models import (
    Badge,
    Discount,
    OrderItem,
)
from registration.tests.common import OrdersTestCase
from registration.views.ordering import get_discount_total, get_line_item_total

tz = timezone.get_current_timezone()
now = timezone.now()
ten_days = timedelta(days=10)
one_day = timedelta(days=1)


class TestOrderingModule(OrdersTestCase):
    def setUp(self):
        super().setUp()

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
