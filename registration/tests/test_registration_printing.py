from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from registration.models import (
    Attendee,
    Badge,
    BadgeTemplate,
    Event,
    Order,
    OrderItem,
    PriceLevel,
)
from registration.tests.common import DEFAULT_EVENT_ARGS, TEST_ATTENDEE_ARGS

now = timezone.now()
ten_days = timedelta(days=10)


class TestRegistrationPrinting(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser("admin", "admin@host", "admin")
        self.admin_user.save()

        self.badge_template = BadgeTemplate(
            template="<script>window.badgeReady = true;</script>"
        )
        self.badge_template.save()

        self.event = Event(
            defaultBadgeTemplate=self.badge_template, **DEFAULT_EVENT_ARGS
        )
        self.event.save()

        self.priceLevel = PriceLevel(
            name="Attendee",
            description="Hello",
            basePrice=1.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=True,
        )
        self.priceLevel.save()

        self.order = Order(
            total=100,
            billingType=Order.CREDIT,
            status=Order.COMPLETED,
            reference="CREDIT_ORDER_1",
        )
        self.order.save()

        self.attendee = Attendee(**TEST_ATTENDEE_ARGS)
        self.attendee.save()

        self.badge = Badge(event=self.event, attendee=self.attendee)
        self.badge.save()

        self.order_item = OrderItem(
            order=self.order,
            badge=self.badge,
            priceLevel=self.priceLevel,
        )
        self.order_item.save()

        self.client = Client()

    def _badge_generates_pdf(self) -> dict:
        self.assertTrue(self.client.login(username="admin", password="admin"))

        response = self.client.get(
            reverse("registration:onsite_print_badges") + f"?id={self.badge.pk}"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNotNone(data["file"])

        self.client.logout()

        response = self.client.get(data["file"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")

        return data

    def test_print_wkhtmltopdf(self):
        settings.PRINT_RENDERER = "wkhtmltopdf"
        data = self._badge_generates_pdf()
        # wkhtmltopdf responses return a direct path to the file
        self.assertIn("?file=", data["file"])

    def test_print_gotenberg(self):
        settings.PRINT_RENDERER = "gotenberg"
        data = self._badge_generates_pdf()
        # gotenberg responses return a signed data parameter with badge IDs
        self.assertIn("?data=", data["file"])
