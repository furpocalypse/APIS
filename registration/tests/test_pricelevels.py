import json
from datetime import timedelta
from typing import Any

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from registration.models import Attendee, Badge, Order, OrderItem
from registration.tests.common import EventFixtureMixin, PriceLevelFixturesMixin

tz = timezone.get_current_timezone()
now = timezone.now()
ten_days = timedelta(days=10)
one_day = timedelta(days=1)
fifteen_years = timedelta(days=365 * 15)
eighteen_years = timedelta(days=365 * 18)
twenty_years = timedelta(days=365 * 20)


class TestOrderingModule(TestCase, EventFixtureMixin, PriceLevelFixturesMixin):
    def setUp(self):
        super().setUp()

        self.add_event_fixtures()
        self.add_pricelevel_fixtures()

        self.price_free.max_age = 12
        self.price_minor_25.public = True
        self.price_minor_25.min_age = 13
        self.price_minor_25.max_age = 17
        self.price_accompanied_0.max_age = 12
        self.price_minor_35.min_age = 13
        self.price_minor_35.max_age = 17
        self.price_45.min_age = 18
        self.price_90.min_age = 18
        self.price_150.min_age = 18
        self.price_235.min_age = 18
        self.price_235.public = True
        self.price_675.min_age = 18

        self.price_free.save()
        self.price_minor_25.save()
        self.price_accompanied_0.save()
        self.price_minor_35.save()
        self.price_45.save()
        self.price_90.save()
        self.price_150.save()
        self.price_235.save()
        self.price_675.save()

        self.adult = Attendee(
            firstName="A",
            lastName="Person",
            phone="1234567890",
            email="someone@somewhere.com",
            birthdate=now - twenty_years,
        )
        self.adult2 = Attendee(
            firstName="Another",
            lastName="Person",
            phone="1234567890",
            email="another@somewhere.com",
            birthdate=now - twenty_years,
        )
        self.minor = Attendee(
            firstName="A",
            lastName="Child",
            phone="1234567890",
            email="child@murderer.com",
            birthdate=now - fifteen_years,
        )
        self.adult.save()
        self.adult2.save()
        self.minor.save()

        self.badge_base = Badge(attendee=self.adult, event=self.event, badgeNumber=123)
        self.badge_tier2 = Badge(attendee=self.adult2, event=self.event, badgeNumber=123)
        self.badge_minor = Badge(attendee=self.minor, event=self.event, badgeNumber=123)
        self.badge_base.save()
        self.badge_tier2.save()
        self.badge_minor.save()

        self.order1 = Order(
            total=self.price_45.basePrice,
            status=Order.COMPLETED,
            reference="asdf",
            billingType=Order.CREDIT,
        )
        self.order2 = Order(
            total=self.price_90.basePrice + self.price_minor_25.basePrice,
            status=Order.COMPLETED,
            reference="asdf",
            billingType=Order.CREDIT,
        )
        self.order1.save()
        self.order2.save()

        self.oitem_base = OrderItem(
            order=self.order1,
            badge=self.badge_base,
            priceLevel=self.price_45,
            enteredBy="Your Mom",
            enteredDate=now,
        )
        self.oitem_tier2 = OrderItem(
            order=self.order2,
            badge=self.badge_tier2,
            priceLevel=self.price_90,
            enteredBy="Your Mom",
            enteredDate=now,
        )
        self.oitem_minor = OrderItem(
            order=self.order2,
            badge=self.badge_minor,
            priceLevel=self.price_minor_25,
            enteredBy="Your Mom",
            enteredDate=now,
        )
        self.oitem_base.save()
        self.oitem_tier2.save()
        self.oitem_minor.save()

    def send_post_request(self, postData: dict[str, Any]):
        return self.client.post(
            reverse("registration:pricelevels"),
            json.dumps(postData),
            content_type="application/json",
        )

    def test_get_all_adult_pricelevels_by_dob(self):
        response = self.send_post_request(
            {
                "year": self.adult.birthdate.year,
                "month": self.adult.birthdate.month,
                "day": self.adult.birthdate.day,
            }
        )
        self.assertEqual(response.status_code, 200)
        pricelevels = response.json()
        self.assertEqual(len(pricelevels), 3)
        self.assertEqual(pricelevels[0]["id"], self.price_45.id)
        self.assertEqual(pricelevels[1]["id"], self.price_90.id)
        self.assertEqual(pricelevels[2]["id"], self.price_235.id)

    def test_get_all_minor_pricelevels_by_dob(self):
        response = self.send_post_request(
            {
                "year": self.minor.birthdate.year,
                "month": self.minor.birthdate.month,
                "day": self.minor.birthdate.day,
            }
        )
        self.assertEqual(response.status_code, 200)
        pricelevels = response.json()
        self.assertEqual(len(pricelevels), 2)
        self.assertEqual(pricelevels[0]["id"], self.price_minor_25.id)
        self.assertEqual(pricelevels[1]["id"], self.price_minor_35.id)

    def test_get_all_adult_pricelevels_by_badge(self):
        response = self.send_post_request({"badge_id": self.adult.id})
        self.assertEqual(response.status_code, 200)
        pricelevels = response.json()
        self.assertEqual(len(pricelevels), 2)
        self.assertEqual(pricelevels[0]["id"], self.price_90.id)
        self.assertEqual(pricelevels[1]["id"], self.price_235.id)

    def test_get_all_minor_pricelevels_by_badge(self):
        response = self.send_post_request(
            {
                "badge_id": self.minor.id,
            }
        )
        self.assertEqual(response.status_code, 200)
        pricelevels = response.json()
        self.assertEqual(len(pricelevels), 1)
        self.assertEqual(pricelevels[0]["id"], self.price_minor_35.id)
