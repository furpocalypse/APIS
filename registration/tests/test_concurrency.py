"""
Concurrency stress tests for registration checkout capacity limits.

These tests verify that select_for_update() properly prevents overselling
when many concurrent threads race to check out for a limited-capacity tier.
Uses TransactionTestCase for real committed transactions (Django's TestCase
wraps everything in one transaction, which would make select_for_update()
ineffective across threads).
"""

import json
import threading
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from registration.models import Cart, Event, Order, PriceLevel
from registration.views.ordering import doZeroCheckout

NUM_THREADS = 50
MAX_CAPACITY = 5


class ConcurrentCheckoutStressTest(TransactionTestCase):
    """Stress test: 50 threads race for 5 slots simultaneously."""

    def setUp(self):
        self.event = Event.objects.create(
            name="Stress Test Event",
            default=True,
            dealerRegStart=timezone.now() - timedelta(days=1),
            dealerRegEnd=timezone.now() + timedelta(days=30),
            staffRegStart=timezone.now() - timedelta(days=1),
            staffRegEnd=timezone.now() + timedelta(days=30),
            attendeeRegStart=timezone.now() - timedelta(days=1),
            attendeeRegEnd=timezone.now() + timedelta(days=30),
            onsiteRegStart=timezone.now() - timedelta(days=1),
            onsiteRegEnd=timezone.now() + timedelta(days=30),
            eventStart=timezone.now().date() + timedelta(days=30),
            eventEnd=timezone.now().date() + timedelta(days=33),
            collectBillingAddress=False,
            collectAddress=False,
        )

        self.limited_tier = PriceLevel.objects.create(
            name="Limited Stress Tier",
            description="Tier with limited capacity for stress testing",
            basePrice=Decimal("0.00"),
            startDate=timezone.now() - timedelta(days=1),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=MAX_CAPACITY,
            public=True,
            available_to_attendee=True,
        )

        # Pre-create one Cart per thread with unique attendee data
        self.carts = []
        for i in range(NUM_THREADS):
            cart_data = {
                "attendee": {
                    "firstName": f"Stress{i}",
                    "lastName": f"Tester{i}",
                    "email": f"stress{i}@example.com",
                    "phone": "555-0000",
                    "birthdate": "1990-01-15",
                    "badgeName": f"StressBadge{i}",
                    "emailsOk": False,
                    "surveyOk": False,
                    "volDepts": "",
                    "asl": False,
                },
                "priceLevel": {
                    "id": self.limited_tier.id,
                    "options": [],
                },
                "event": self.event.name,
            }
            cart = Cart.objects.create(
                form="Attendee",
                formData=json.dumps(cart_data),
                formHeaders="{}",
            )
            self.carts.append(cart)

    def test_concurrent_zero_checkouts_no_overselling(self):
        """50 threads race for 5 slots — exactly 5 must succeed, 45 must fail."""
        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(NUM_THREADS)

        def checkout_worker(cart):
            try:
                # All threads wait here until every thread is ready
                barrier.wait(timeout=10)
                status, message, _order = doZeroCheckout(None, [cart], [])
                with results_lock:
                    results.append((status, message))
            except Exception as e:
                with results_lock:
                    results.append((False, f"exception: {e}"))
            finally:
                # Each thread gets its own DB connection; close it to avoid leaks
                connection.close()

        threads = []
        for i in range(NUM_THREADS):
            t = threading.Thread(target=checkout_worker, args=(self.carts[i],))
            threads.append(t)

        # Start all threads (they'll block at the barrier until all are ready)
        for t in threads:
            t.start()

        # Wait for all threads to finish
        for t in threads:
            t.join(timeout=30)

        # All threads should have reported a result
        self.assertEqual(
            len(results),
            NUM_THREADS,
            f"Expected {NUM_THREADS} results, got {len(results)}",
        )

        successes = [(s, m) for s, m in results if s]
        failures = [(s, m) for s, m in results if not s]

        # Exactly MAX_CAPACITY should succeed
        self.assertEqual(
            len(successes),
            MAX_CAPACITY,
            f"Expected {MAX_CAPACITY} successes, got {len(successes)}. Failures: {failures[:5]}...",
        )

        # All failures should be "sold out"
        for _status, message in failures:
            self.assertIn(
                "sold out",
                message.lower(),
                f"Unexpected failure reason: {message}",
            )

        # Verify database integrity — the ground truth
        self.limited_tier.refresh_from_db()
        reg_count = self.limited_tier.get_registration_count()
        self.assertEqual(
            reg_count,
            MAX_CAPACITY,
            f"Database has {reg_count} registrations, expected {MAX_CAPACITY}",
        )

        # Verify counters match ground truth (no drift from concurrency)
        self.assertTrue(
            self.limited_tier.verify_and_repair_counters(),
            f"Counter drift after concurrent checkouts: "
            f"remainingSlots={self.limited_tier.remainingSlots}, "
            f"reservedSlots={self.limited_tier.reservedSlots}",
        )

        # Verify Order count matches
        completed_orders = Order.objects.filter(status=Order.COMPLETED).count()
        self.assertEqual(
            completed_orders,
            MAX_CAPACITY,
            f"Found {completed_orders} completed orders, expected {MAX_CAPACITY}",
        )
