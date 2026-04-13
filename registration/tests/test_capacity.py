"""
Tests for registration tier capacity limits functionality.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from registration.models import Event, Order, PriceLevel
from registration.tests.common import CapacityTestMixin
from registration.views import ordering


class PriceLevelCapacityTestCase(CapacityTestMixin, TestCase):
    """Test PriceLevel capacity methods."""

    def setUp(self):
        self.event = Event.objects.create(
            name="Test Event 2026",
            default=True,
            dealerRegStart=timezone.now(),
            dealerRegEnd=timezone.now() + timedelta(days=30),
            staffRegStart=timezone.now(),
            staffRegEnd=timezone.now() + timedelta(days=30),
            attendeeRegStart=timezone.now(),
            attendeeRegEnd=timezone.now() + timedelta(days=30),
            onsiteRegStart=timezone.now(),
            onsiteRegEnd=timezone.now() + timedelta(days=30),
            eventStart=timezone.now().date(),
            eventEnd=timezone.now().date() + timedelta(days=3),
        )

        self.limited_tier = PriceLevel.objects.create(
            name="Limited Tier",
            description="Test tier with capacity limit",
            basePrice=Decimal("50.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=5,
            capacityDisplayThreshold=2,
        )

        self.unlimited_tier = PriceLevel.objects.create(
            name="Unlimited Tier",
            description="Test tier without capacity limit",
            basePrice=Decimal("75.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=None,
        )

    def test_get_registration_count_empty(self):
        """Test that get_registration_count returns 0 when no orders."""
        self.assertEqual(self.limited_tier.get_registration_count(), 0)

    def test_get_registration_count_with_completed_orders(self):
        """Test counting OrderItems from completed orders."""
        # Create orders with items
        self._create_order_with_items(self.limited_tier, 2, Order.COMPLETED)
        self._create_order_with_items(self.limited_tier, 1, Order.COMPLETED)

        self.assertEqual(self.limited_tier.get_registration_count(), 3)

    def test_get_registration_count_excludes_pending_orders(self):
        """Test that pending orders don't count as confirmed registrations."""
        self._create_order_with_items(self.limited_tier, 2, Order.PENDING)
        self.assertEqual(self.limited_tier.get_registration_count(), 0)
        self.assertEqual(self.limited_tier.get_pending_count(), 2)

    def test_get_registration_count_excludes_failed_orders(self):
        """Test that failed orders don't count toward capacity."""
        self._create_order_with_items(self.limited_tier, 2, Order.COMPLETED)
        self._create_order_with_items(self.limited_tier, 1, Order.FAILED)

        self.assertEqual(self.limited_tier.get_registration_count(), 2)

    def test_get_registration_count_excludes_refunded_orders(self):
        """Test that refunded orders don't count toward capacity."""
        self._create_order_with_items(self.limited_tier, 2, Order.COMPLETED)
        self._create_order_with_items(self.limited_tier, 1, Order.REFUNDED)

        self.assertEqual(self.limited_tier.get_registration_count(), 2)

    def test_get_registration_count_includes_captured_orders(self):
        """Test that captured orders count toward capacity."""
        self._create_order_with_items(self.limited_tier, 1, Order.CAPTURED)
        self._create_order_with_items(self.limited_tier, 1, Order.COMPLETED)

        self.assertEqual(self.limited_tier.get_registration_count(), 2)

    def test_get_available_slots_limited(self):
        """Test available slots calculation for limited tier."""
        self.assertEqual(self.limited_tier.get_available_slots(), 5)

        self._create_order_with_items(self.limited_tier, 3)
        self.assertEqual(self.limited_tier.get_available_slots(), 2)

    def test_get_available_slots_unlimited(self):
        """Test available slots returns None for unlimited tier."""
        self.assertIsNone(self.unlimited_tier.get_available_slots())

    def test_is_sold_out_false(self):
        """Test is_sold_out returns False when capacity available."""
        self.assertFalse(self.limited_tier.is_sold_out())

        self._create_order_with_items(self.limited_tier, 4)
        self.assertFalse(self.limited_tier.is_sold_out())

    def test_is_sold_out_true(self):
        """Test is_sold_out returns True when at capacity."""
        self._create_order_with_items(self.limited_tier, 5)
        self.assertTrue(self.limited_tier.is_sold_out())

    def test_is_sold_out_unlimited(self):
        """Test is_sold_out returns False for unlimited tier."""
        self._create_order_with_items(self.unlimited_tier, 1000)
        self.assertFalse(self.unlimited_tier.is_sold_out())

    def test_get_capacity_status_limited(self):
        """Test capacity status for limited tier."""
        status = self.limited_tier.get_capacity_status()
        self.assertEqual(status["available_slots"], 5)
        self.assertEqual(status["max_capacity"], 5)
        self.assertFalse(status["sold_out"])
        self.assertFalse(status["show_count"])

        # Test with low capacity
        self._create_order_with_items(self.limited_tier, 4)
        status = self.limited_tier.get_capacity_status()
        self.assertEqual(status["available_slots"], 1)
        self.assertTrue(status["show_count"])  # Below threshold of 2

    def test_get_capacity_status_unlimited(self):
        """Test capacity status for unlimited tier."""
        status = self.unlimited_tier.get_capacity_status()
        self.assertIsNone(status["available_slots"])
        self.assertIsNone(status["max_capacity"])
        self.assertFalse(status["sold_out"])
        self.assertFalse(status["show_count"])

    def test_check_capacity_available_success(self):
        """Test capacity check succeeds when slots available."""
        result = self.limited_tier.check_capacity_available(1)
        self.assertEqual(result, PriceLevel.CAPACITY_AVAILABLE)

        result = self.limited_tier.check_capacity_available(5)
        self.assertEqual(result, PriceLevel.CAPACITY_AVAILABLE)

    def test_check_capacity_available_sold_out(self):
        """Test capacity check returns SOLD_OUT when all completed."""
        self._create_order_with_items(self.limited_tier, 5, Order.COMPLETED)

        result = self.limited_tier.check_capacity_available(1)
        self.assertEqual(result, PriceLevel.CAPACITY_SOLD_OUT)

    def test_check_capacity_available_reserved(self):
        """Test capacity check returns RESERVED when pending orders hold slots."""
        self._create_order_with_items(self.limited_tier, 3, Order.COMPLETED)
        self._create_order_with_items(self.limited_tier, 2, Order.PENDING)

        result = self.limited_tier.check_capacity_available(1)
        self.assertEqual(result, PriceLevel.CAPACITY_RESERVED)

    def test_check_capacity_available_insufficient_slots(self):
        """Test capacity check fails when insufficient slots available."""
        self._create_order_with_items(self.limited_tier, 3)

        # Try to reserve 3 when only 2 available
        result = self.limited_tier.check_capacity_available(3)
        self.assertEqual(result, PriceLevel.CAPACITY_SOLD_OUT)

    def test_check_capacity_available_unlimited(self):
        """Test capacity check always succeeds for unlimited tier."""
        result = self.unlimited_tier.check_capacity_available(10000)
        self.assertEqual(result, PriceLevel.CAPACITY_AVAILABLE)


class CheckoutCapacityTestCase(CapacityTestMixin, TestCase):
    """Test checkout functions with capacity validation."""

    def setUp(self):
        self.event = Event.objects.create(
            name="Test Event 2026",
            default=True,
            dealerRegStart=timezone.now(),
            dealerRegEnd=timezone.now() + timedelta(days=30),
            staffRegStart=timezone.now(),
            staffRegEnd=timezone.now() + timedelta(days=30),
            attendeeRegStart=timezone.now(),
            attendeeRegEnd=timezone.now() + timedelta(days=30),
            onsiteRegStart=timezone.now(),
            onsiteRegEnd=timezone.now() + timedelta(days=30),
            eventStart=timezone.now().date(),
            eventEnd=timezone.now().date() + timedelta(days=3),
        )

        self.limited_tier = PriceLevel.objects.create(
            name="Limited Tier",
            description="Test tier with capacity limit",
            basePrice=Decimal("50.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=3,
            capacityDisplayThreshold=2,
        )

        self.unlimited_tier = PriceLevel.objects.create(
            name="Unlimited Tier",
            description="Test tier without capacity limit",
            basePrice=Decimal("75.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=None,
        )

    def test_do_checkout_fails_when_sold_out(self):
        """Test that do_checkout fails when tier is sold out."""
        # Fill capacity with completed orders
        self._create_completed_order(self.limited_tier, 3)
        self.assertTrue(self.limited_tier.is_sold_out())

        cart_item = self._create_cart_item(self.limited_tier)

        billing_data = {
            "cc_firstname": "Test",
            "cc_lastname": "User",
            "email": "test@example.com",
            "address1": "123 Main St",
            "address2": "",
            "city": "Test City",
            "state": "TS",
            "country": "US",
            "postal": "12345",
        }

        status, message, order = ordering.do_checkout(
            billing_data,
            Decimal("50.00"),
            None,
            [cart_item],
            [],
            Decimal("0.00"),
            Decimal("0.00"),
            None,
        )

        self.assertFalse(status)
        self.assertIn("sold out", message)
        self.assertIsNone(order)

    def test_do_checkout_fails_insufficient_capacity(self):
        """Test checkout fails when multiple items exceed capacity."""
        # Use 2 of 3 slots
        self._create_completed_order(self.limited_tier, 2)

        # Try to checkout 2 more items (only 1 slot available)
        cart_item1 = self._create_cart_item(self.limited_tier)
        cart_item2 = self._create_cart_item(self.limited_tier)

        billing_data = {
            "cc_firstname": "Test",
            "cc_lastname": "User",
            "email": "test@example.com",
            "address1": "123 Main St",
            "address2": "",
            "city": "Test City",
            "state": "TS",
            "country": "US",
            "postal": "12345",
        }

        status, message, order = ordering.do_checkout(
            billing_data,
            Decimal("100.00"),
            None,
            [cart_item1, cart_item2],
            [],
            Decimal("0.00"),
            Decimal("0.00"),
            None,
        )

        self.assertFalse(status)
        self.assertIn("sold out", message)

        # Count should still be 2 (no partial reservation)
        self.assertEqual(self.limited_tier.get_registration_count(), 2)

    def test_zero_checkout_reserves_capacity(self):
        """Test that doZeroCheckout creates OrderItems that hold capacity."""
        cart_item = self._create_cart_item(self.limited_tier)

        # Before checkout
        self.assertEqual(self.limited_tier.get_registration_count(), 0)

        status, message, order = ordering.doZeroCheckout(None, [cart_item], [])

        self.assertTrue(status)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, Order.COMPLETED)

        # After successful zero checkout, capacity should be held by OrderItem
        self.limited_tier.refresh_from_db()
        self.assertEqual(self.limited_tier.get_registration_count(), 1)

    def test_zero_checkout_fails_when_sold_out(self):
        """Test that doZeroCheckout fails when tier is sold out."""
        # Fill capacity
        self._create_completed_order(self.limited_tier, 3)

        cart_item = self._create_cart_item(self.limited_tier)

        status, message, order = ordering.doZeroCheckout(None, [cart_item], [])

        self.assertFalse(status)
        self.assertIn("sold out", message)
        self.assertIsNone(order)

        # Count should still be 3
        self.assertEqual(self.limited_tier.get_registration_count(), 3)

    def test_unlimited_tier_checkout(self):
        """Test that unlimited tier always allows checkout."""
        # Create many cart items
        cart_items = [self._create_cart_item(self.unlimited_tier) for _ in range(10)]

        status, message, order = ordering.doZeroCheckout(None, cart_items, [])

        self.assertTrue(status)
        self.assertIsNotNone(order)

        # Should have 10 OrderItems (use ground-truth for unlimited tiers)
        self.assertEqual(self.unlimited_tier.verify_registration_count(), 10)

    def test_pending_order_blocks_checkout_but_not_display(self):
        """Test that PENDING orders block checkout but don't affect frontend counts."""
        from registration.payments import update_capacity_for_status_change

        # 2 confirmed + 1 pending against capacity of 3
        self._create_completed_order(self.limited_tier, 2)
        pending_order = self._create_order_with_items(
            self.limited_tier, 1, Order.PENDING
        )

        # Frontend sees only confirmed registrations
        self.limited_tier.refresh_from_db()
        self.assertEqual(self.limited_tier.get_registration_count(), 2)
        self.assertEqual(self.limited_tier.get_available_slots(), 1)
        self.assertFalse(self.limited_tier.is_sold_out())
        # Pending is tracked separately
        self.assertEqual(self.limited_tier.get_pending_count(), 1)

        # But checkout is blocked — 2 confirmed + 1 pending = 3 = capacity
        self.assertEqual(
            self.limited_tier.check_capacity_available(1),
            PriceLevel.CAPACITY_RESERVED,
        )

    def test_pending_to_completed_confirms_slot(self):
        """Test that a PENDING→COMPLETED transition confirms the reserved slot."""
        from registration.payments import update_capacity_for_status_change

        self._create_completed_order(self.limited_tier, 2)
        pending_order = self._create_order_with_items(
            self.limited_tier, 1, Order.PENDING
        )

        old_status = pending_order.status
        pending_order.status = Order.COMPLETED
        update_capacity_for_status_change(
            pending_order, old_status, pending_order.status
        )
        pending_order.save()
        self.limited_tier.refresh_from_db()
        self.assertEqual(self.limited_tier.get_registration_count(), 3)
        self.assertEqual(self.limited_tier.get_pending_count(), 0)
        self.assertEqual(
            self.limited_tier.check_capacity_available(1),
            PriceLevel.CAPACITY_SOLD_OUT,
        )

    def test_pending_to_failed_releases_slot(self):
        """Test that a PENDING→FAILED transition releases the reserved slot."""
        from registration.payments import update_capacity_for_status_change

        self._create_completed_order(self.limited_tier, 2)
        pending_order = self._create_order_with_items(
            self.limited_tier, 1, Order.PENDING
        )

        old_status = pending_order.status
        pending_order.status = Order.FAILED
        update_capacity_for_status_change(
            pending_order, old_status, pending_order.status
        )
        pending_order.save()
        self.limited_tier.refresh_from_db()
        self.assertEqual(self.limited_tier.get_registration_count(), 2)
        self.assertEqual(self.limited_tier.get_pending_count(), 0)
        self.assertEqual(
            self.limited_tier.check_capacity_available(1),
            PriceLevel.CAPACITY_AVAILABLE,
        )


class CounterMethodsTestCase(CapacityTestMixin, TestCase):
    """Test counter manipulation and verification methods directly."""

    def setUp(self):
        self.event = Event.objects.create(
            name="Test Event 2026",
            default=True,
            dealerRegStart=timezone.now(),
            dealerRegEnd=timezone.now() + timedelta(days=30),
            staffRegStart=timezone.now(),
            staffRegEnd=timezone.now() + timedelta(days=30),
            attendeeRegStart=timezone.now(),
            attendeeRegEnd=timezone.now() + timedelta(days=30),
            onsiteRegStart=timezone.now(),
            onsiteRegEnd=timezone.now() + timedelta(days=30),
            eventStart=timezone.now().date(),
            eventEnd=timezone.now().date() + timedelta(days=3),
        )

        self.limited_tier = PriceLevel.objects.create(
            name="Limited Tier",
            description="Test tier with capacity limit",
            basePrice=Decimal("50.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=10,
        )

    def test_initial_counters(self):
        """New PriceLevel with maxCapacity gets correct initial counters."""
        self.assertEqual(self.limited_tier.remainingSlots, 10)
        self.assertEqual(self.limited_tier.reservedSlots, 0)

    def test_unlimited_tier_has_null_counters(self):
        unlimited = PriceLevel.objects.create(
            name="Unlimited",
            description="No limit",
            basePrice=Decimal("50.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=None,
        )
        self.assertIsNone(unlimited.remainingSlots)
        self.assertEqual(unlimited.reservedSlots, 0)

    def test_reserve_slots(self):
        self.limited_tier.reserve_slots(3)
        self.assertEqual(self.limited_tier.remainingSlots, 7)
        self.assertEqual(self.limited_tier.reservedSlots, 3)

    def test_confirm_reservation(self):
        self.limited_tier.reserve_slots(3)
        self.limited_tier.confirm_reservation(3)
        self.assertEqual(self.limited_tier.remainingSlots, 7)
        self.assertEqual(self.limited_tier.reservedSlots, 0)

    def test_release_slots(self):
        self.limited_tier.reserve_slots(3)
        self.limited_tier.release_slots(2)
        self.assertEqual(self.limited_tier.remainingSlots, 9)
        self.assertEqual(self.limited_tier.reservedSlots, 1)

    def test_release_confirmed_slots(self):
        self.limited_tier.reserve_slots(3)
        self.limited_tier.confirm_reservation(3)
        self.limited_tier.release_confirmed_slots(1)
        self.assertEqual(self.limited_tier.remainingSlots, 8)
        self.assertEqual(self.limited_tier.reservedSlots, 0)

    def test_consume_slots(self):
        self.limited_tier.consume_slots(3)
        self.assertEqual(self.limited_tier.remainingSlots, 7)
        self.assertEqual(self.limited_tier.reservedSlots, 0)

    def test_unlimited_consume_is_noop(self):
        unlimited = PriceLevel.objects.create(
            name="Unlimited",
            description="No limit",
            basePrice=Decimal("50.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=None,
        )
        unlimited.consume_slots(100)
        self.assertIsNone(unlimited.remainingSlots)
        self.assertEqual(unlimited.reservedSlots, 0)

    def test_unlimited_reserve_is_noop(self):
        unlimited = PriceLevel.objects.create(
            name="Unlimited",
            description="No limit",
            basePrice=Decimal("50.00"),
            startDate=timezone.now(),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=None,
        )
        unlimited.reserve_slots(100)
        self.assertIsNone(unlimited.remainingSlots)
        self.assertEqual(unlimited.reservedSlots, 0)

    def test_verify_and_repair_detects_drift(self):
        """Manually corrupt counters and verify repair."""
        self.limited_tier.remainingSlots = 999
        self.limited_tier.save(update_fields=["remainingSlots"])

        is_correct = self.limited_tier.verify_and_repair_counters()
        self.assertFalse(is_correct)
        # After repair, counters should match ground truth
        self.assertEqual(self.limited_tier.remainingSlots, 10)
        self.assertEqual(self.limited_tier.reservedSlots, 0)

    def test_verify_returns_true_when_correct(self):
        self.assertTrue(self.limited_tier.verify_and_repair_counters())

    def test_max_capacity_change_recalculates_counters(self):
        """Changing maxCapacity via save() recalculates counters."""
        # Create 3 real confirmed orders (mixin syncs counters automatically)
        self._create_order_with_items(self.limited_tier, 3, Order.COMPLETED)
        self.assertEqual(self.limited_tier.remainingSlots, 7)

        # Change capacity from 10 to 15
        self.limited_tier.maxCapacity = 15
        self.limited_tier.save()
        self.limited_tier.refresh_from_db()
        # Should recalculate: 15 - 3 confirmed - 0 pending = 12
        self.assertEqual(self.limited_tier.remainingSlots, 12)
        self.assertEqual(self.limited_tier.reservedSlots, 0)
