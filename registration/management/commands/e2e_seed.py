"""Seed the database with the minimum reference data required for the
Playwright E2E suite to run.

Mirrors :class:`registration.tests.common.OrdersTestCase.setUp` but as a
management command so the live server (driven by the Playwright suite) sees
the same PriceLevels, Discounts, Event, TableSizes, Departments, and admin
user that the Django test harness does.

Safe to call repeatedly: uses ``get_or_create`` throughout.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from registration import models


class Command(BaseCommand):
    help = "Seed reference data used by the Playwright E2E suite."

    def handle(self, *args, **options):
        with transaction.atomic():
            seed()
        self.stdout.write(self.style.SUCCESS("e2e seed complete"))


def seed() -> None:
    now = timezone.now()
    window_open = now - timedelta(days=10)
    window_close = now + timedelta(days=10)

    _upsert_price_level(
        name="Free",
        base=Decimal("0.00"),
        public=False,
        is_minor=True,
        start=window_open,
        end=window_close,
    )
    _upsert_price_level(
        name="Minor",
        base=Decimal("35.00"),
        public=True,
        is_minor=True,
        start=window_open,
        end=window_close,
    )
    price_45 = _upsert_price_level(
        name="Attendee",
        base=Decimal("45.00"),
        public=True,
        is_minor=False,
        start=window_open,
        end=window_close,
    )
    price_90 = _upsert_price_level(
        name="Sponsor",
        base=Decimal("90.00"),
        public=True,
        is_minor=False,
        start=window_open,
        end=window_close,
    )
    _upsert_price_level(
        name="Super",
        base=Decimal("150.00"),
        public=True,
        is_minor=False,
        start=window_open,
        end=window_close,
    )

    conbook, _ = models.PriceLevelOption.objects.get_or_create(
        optionName="Conbook",
        defaults={"optionPrice": Decimal("0.00"), "optionExtraType": "bool"},
    )
    shirt_opt, _ = models.PriceLevelOption.objects.get_or_create(
        optionName="Shirt Size",
        defaults={"optionPrice": Decimal("0.00"), "optionExtraType": "ShirtSizes"},
    )
    price_45.priceLevelOptions.add(conbook, shirt_opt)
    price_90.priceLevelOptions.add(conbook)

    models.ShirtSizes.objects.get_or_create(name="Medium")
    models.ShirtSizes.objects.get_or_create(name="Large")

    models.Department.objects.get_or_create(name="Operations")
    models.Department.objects.get_or_create(name="Media")

    staff_discount, _ = models.Discount.objects.get_or_create(
        codeName="StaffDiscount",
        defaults={
            "amountOff": Decimal("45.00"),
            "startDate": window_open,
            "endDate": window_close,
        },
    )
    dealer_discount, _ = models.Discount.objects.get_or_create(
        codeName="DealerDiscount",
        defaults={
            "amountOff": Decimal("45.00"),
            "startDate": window_open,
            "endDate": window_close,
        },
    )
    models.Discount.objects.get_or_create(
        codeName="FiveOff",
        defaults={
            "amountOff": Decimal("5.00"),
            "startDate": window_open,
            "endDate": window_close,
        },
    )
    models.Discount.objects.get_or_create(
        codeName="OneTime",
        defaults={
            "percentOff": 10,
            "oneTime": True,
            "startDate": window_open,
            "endDate": window_close,
        },
    )

    event, _ = models.Event.objects.get_or_create(
        default=True,
        defaults={
            "name": "APIS E2E Event",
            "dealerRegStart": window_open,
            "dealerRegEnd": window_close,
            "staffRegStart": window_open,
            "staffRegEnd": window_close,
            "attendeeRegStart": window_open,
            "attendeeRegEnd": window_close,
            "onsiteRegStart": window_open,
            "onsiteRegEnd": window_close,
            "eventStart": window_open,
            "eventEnd": window_close,
        },
    )
    event.staffDiscount = staff_discount
    event.dealerDiscount = dealer_discount
    event.save()

    models.TableSize.objects.get_or_create(
        name="Booth",
        defaults={
            "description": "E2E booth",
            "chairMin": 0,
            "chairMax": 1,
            "tableMin": 0,
            "tableMax": 1,
            "partnerMin": 0,
            "partnerMax": 1,
            "basePrice": Decimal("130.00"),
        },
    )

    User = get_user_model()
    admin, created = User.objects.get_or_create(
        username="e2e-admin",
        defaults={
            "email": "e2e-admin@example.com",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    if created:
        admin.set_password("e2e-admin-password")
        admin.save()


def _upsert_price_level(
    *,
    name: str,
    base: Decimal,
    public: bool,
    is_minor: bool,
    start,
    end,
) -> models.PriceLevel:
    obj, _ = models.PriceLevel.objects.get_or_create(
        name=name,
        basePrice=base,
        defaults={
            "description": f"{name} seeded by e2e_seed",
            "startDate": start,
            "endDate": end,
            "public": public,
            "isMinor": is_minor,
            "available_to_attendee": True,
        },
    )
    return obj
