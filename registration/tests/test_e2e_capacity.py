"""
End-to-end Playwright tests for registration tier capacity limits.

These tests verify that the frontend correctly displays capacity information
and handles sold-out scenarios, including concurrent checkout race conditions.
"""

import os
from datetime import timedelta
from decimal import Decimal

# Required for StaticLiveServerTestCase which runs a threaded server —
# Django's async safety check would otherwise block ORM calls in setUp/tearDown.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from playwright.sync_api import sync_playwright

from registration.models import Event, Order, PriceLevel
from registration.tests.common import CapacityTestMixin


class E2ETestMixin(CapacityTestMixin):
    """Shared helpers for E2E Playwright tests.

    Provides Playwright browser lifecycle, form-filling helpers, and
    test data factories. Subclasses must also inherit from
    StaticLiveServerTestCase (which provides self.live_server_url).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls.playwright.stop()
            if "Executable doesn't exist" in str(exc):
                raise RuntimeError(
                    "Playwright browsers are not installed. Run:\n\n"
                    "    uv run --group test playwright install --with-deps\n"
                ) from exc
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def _new_page(self):
        """Create a new browser context and page (isolated session)."""
        context = self.browser.new_context()
        page = context.new_page()
        return context, page

    def _enter_birthdate(self, page):
        """Enter birthdate fields (year, month, day) and wait for day options."""
        page.fill("#byear", "1990")
        page.select_option("#bmonth", "01")
        # Day is a <select> populated by date-entry.js after year+month change.
        # Wait for the options to be added to the DOM (not necessarily visible).
        page.wait_for_selector(
            "#bday option[value='15']", state="attached", timeout=5000
        )
        page.select_option("#bday", "15")

    def _fill_registration_form(self, page, suffix=""):
        """Fill out the registration form (does not submit)."""
        page.goto(f"{self.live_server_url}/registration/")
        page.wait_for_selector("form[role='form']", state="visible")

        page.fill("#firstName", f"Test{suffix}")
        page.fill("#lastName", f"User{suffix}")
        page.fill("#email", f"test{suffix}@example.com")
        page.fill("#phone", "555-1234")

        self._enter_birthdate(page)
        page.wait_for_selector("a.selectLevel", state="visible", timeout=10000)

        page.fill("#badgeName", f"TestBadge{suffix}")
        return page

    def _sign_signature(self, page):
        """Draw a single-line signature on the jSignature canvas."""
        sig = page.locator("#jsign_signature")
        box = sig.bounding_box()
        page.mouse.move(box["x"] + 20, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] - 20, box["y"] + box["height"] / 2)
        page.mouse.up()

    def _select_tier_and_register(self, page, tier_name):
        """Select a tier, agree to CoC, sign, and submit the registration form."""
        panels = page.locator(".panel.price")
        for i in range(panels.count()):
            panel = panels.nth(i)
            heading = panel.locator(".panel-heading h3").text_content()
            if tier_name in heading:
                panel.locator("a.selectLevel").click()
                break

        page.check("#agreeToRules")
        self._sign_signature(page)

        # Click register — the JS does an AJAX POST then redirects via window.location
        page.click("#register")
        page.wait_for_url("**/registration/cart**", timeout=15000)


class E2ECapacityTestCase(E2ETestMixin, StaticLiveServerTestCase):
    """E2E tests for registration capacity display and checkout behavior."""

    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(
            name="Test Event 2050",
            default=True,
            dealerRegStart=timezone.now() - timedelta(days=10),
            dealerRegEnd=timezone.now() + timedelta(days=10),
            staffRegStart=timezone.now() - timedelta(days=10),
            staffRegEnd=timezone.now() + timedelta(days=10),
            attendeeRegStart=timezone.now() - timedelta(days=10),
            attendeeRegEnd=timezone.now() + timedelta(days=10),
            onsiteRegStart=timezone.now() - timedelta(days=10),
            onsiteRegEnd=timezone.now() + timedelta(days=10),
            eventStart=timezone.now().date() + timedelta(days=30),
            eventEnd=timezone.now().date() + timedelta(days=33),
            collectBillingAddress=False,
            collectAddress=False,
        )

        self.limited_tier = PriceLevel.objects.create(
            name="Limited Tier",
            description="A tier with limited capacity",
            basePrice=Decimal("0.00"),
            startDate=timezone.now() - timedelta(days=1),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=2,
            capacityDisplayThreshold=5,
            public=True,
            available_to_attendee=True,
        )

        # A second tier so the UI shows the tier selection list
        # (with only 1 tier, JS auto-selects it and skips the list view)
        self.unlimited_tier = PriceLevel.objects.create(
            name="Unlimited Tier",
            description="A tier without capacity limit",
            basePrice=Decimal("0.00"),
            startDate=timezone.now() - timedelta(days=1),
            endDate=timezone.now() + timedelta(days=30),
            public=True,
            available_to_attendee=True,
        )

    def _checkout_zero_cost(self, page):
        """Click checkout on the cart page for a zero-cost order."""
        page.wait_for_selector("#checkout", state="visible")
        page.click("#checkout")

    # ---- Test cases ----

    def test_sold_out_tier_shows_sold_out_badge(self):
        """Verify a sold-out tier displays SOLD OUT badge and disabled button."""
        self._create_completed_order(self.limited_tier, 2)

        context, page = self._new_page()
        try:
            page.goto(f"{self.live_server_url}/registration/")
            page.wait_for_selector("form[role='form']", state="visible")

            self._enter_birthdate(page)
            page.wait_for_selector(".capacity-badge", state="visible", timeout=10000)

            # Check for SOLD OUT badge
            sold_out_badge = page.locator(".capacity-badge.sold-out").first
            self.assertTrue(sold_out_badge.is_visible())
            self.assertIn("SOLD OUT", sold_out_badge.text_content())

            # Check that the Limited Tier's select button shows "Sold Out" and is disabled
            # With 2+ tiers visible, the buttons stay in list mode
            limited_btn = page.locator("a.selectLevel:has-text('Sold Out')").first
            self.assertTrue(limited_btn.is_visible())
            self.assertTrue(
                "disabled" in (limited_btn.get_attribute("class") or "")
                or limited_btn.get_attribute("disabled") is not None,
            )
        finally:
            context.close()

    def test_limited_availability_shows_remaining_slots(self):
        """Verify a tier near capacity shows remaining slot count."""
        self._create_completed_order(self.limited_tier, 1)

        context, page = self._new_page()
        try:
            page.goto(f"{self.live_server_url}/registration/")
            page.wait_for_selector("form[role='form']", state="visible")

            self._enter_birthdate(page)
            page.wait_for_selector(".capacity-badge", state="visible", timeout=10000)

            badge = page.locator(".capacity-badge.limited-availability").first
            self.assertTrue(badge.is_visible())
            self.assertIn("1 slot remaining", badge.text_content())
        finally:
            context.close()

    def test_checkout_fails_when_tier_becomes_sold_out(self):
        """Verify checkout shows error when tier sells out between cart and checkout."""
        # Start with 1 slot available
        self._create_completed_order(self.limited_tier, 1)

        context, page = self._new_page()
        try:
            # Fill form and add to cart
            self._fill_registration_form(page, suffix="A")
            self._select_tier_and_register(page, tier_name="Limited Tier")

            # Now consume the last slot via ORM (simulating another user)
            self._create_completed_order(self.limited_tier, 1)

            # Attempt checkout — should fail
            self._checkout_zero_cost(page)

            # Wait for error to appear
            alert_bar = page.locator("#alert-bar")
            alert_bar.wait_for(state="visible", timeout=10000)
            alert_text = alert_bar.text_content()
            self.assertIn("sold out", alert_text.lower())
        finally:
            context.close()

    def test_checkout_capacity_warning_displayed(self):
        """Verify the 'Limited Availability' warning appears on checkout page."""
        # Fill 1 of 2 slots so we're below threshold
        self._create_completed_order(self.limited_tier, 1)

        context, page = self._new_page()
        try:
            self._fill_registration_form(page, suffix="W")
            self._select_tier_and_register(page, tier_name="Limited Tier")

            # On the checkout page, check for capacity warning
            warning = page.locator(".capacity-warning")
            self.assertTrue(warning.is_visible())
            self.assertIn("Limited Availability", warning.text_content())
        finally:
            context.close()

    def _wait_for_checkout_result(self, page, timeout=15000):
        """Wait for zero-cost checkout to resolve: redirect to done or error alert.

        Returns (succeeded: bool, error_text: str or None).
        """
        # After clicking checkout, the JS either:
        #   - redirects via window.location (success)
        #   - shows #alert-bar (failure)
        # We race both signals and return whichever fires first.
        try:
            page.wait_for_url("**/cart/done**", timeout=timeout)
            return True, None
        except Exception as nav_err:
            # Navigation didn't happen — check for an error alert instead
            alert = page.locator("#alert-bar:not(.alert-hidden)")
            if alert.is_visible():
                return False, alert.text_content()
            return False, f"No redirect or alert after {timeout}ms: {nav_err}"

    def test_concurrent_checkouts_one_succeeds_one_fails(self):
        """Verify that when two users race for the last slot, exactly one succeeds."""
        # Fill 1 of 2 slots
        self._create_completed_order(self.limited_tier, 1)

        context_a, page_a = self._new_page()
        context_b, page_b = self._new_page()
        try:
            # Both users fill forms and add to cart
            self._fill_registration_form(page_a, suffix="RaceA")
            self._select_tier_and_register(page_a, tier_name="Limited Tier")

            self._fill_registration_form(page_b, suffix="RaceB")
            self._select_tier_and_register(page_b, tier_name="Limited Tier")

            # Both are now on the checkout page. Check out sequentially —
            # with select_for_update() on the PriceLevel row, the second
            # checkout will see the updated count and fail.

            # User A checks out first
            self._checkout_zero_cost(page_a)
            a_succeeded, a_error = self._wait_for_checkout_result(page_a)

            # User B checks out second
            self._checkout_zero_cost(page_b)
            b_succeeded, b_error = self._wait_for_checkout_result(page_b)

            # Exactly one should succeed
            self.assertTrue(
                a_succeeded and not b_succeeded,
                f"Expected A to succeed and B to fail. "
                f"A: succeeded={a_succeeded}, B: succeeded={b_succeeded}, "
                f"A error: {a_error}, B error: {b_error}",
            )

            # The failing user should see "sold out"
            self.assertIsNotNone(b_error)
            self.assertIn("sold out", b_error.lower())

            # Database should have exactly 2 registrations (1 pre-existing + 1 new)
            self.limited_tier.refresh_from_db()
            self.assertEqual(self.limited_tier.get_registration_count(), 2)
        finally:
            context_a.close()
            context_b.close()


class E2EPaidCheckoutTestCase(E2ETestMixin, StaticLiveServerTestCase):
    """E2E tests for paid checkout with real Square sandbox payment processing.

    Uses Square's sandbox SDK with test card numbers. Playwright interacts
    with the Square card form iframe directly (cross-origin iframe access).

    Square sandbox test card: 4111 1111 1111 1111, CVV 111, any future exp.
    """

    # Square sandbox test card details
    TEST_CARD_NUMBER = "4111111111111111"
    TEST_CARD_EXP = "12/30"
    TEST_CARD_CVV = "111"
    TEST_CARD_ZIP = "94103"

    # Card number that Square sandbox will decline
    DECLINED_CARD_NUMBER = "4000000000000002"

    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(
            name="Test Event 2050",
            default=True,
            dealerRegStart=timezone.now() - timedelta(days=10),
            dealerRegEnd=timezone.now() + timedelta(days=10),
            staffRegStart=timezone.now() - timedelta(days=10),
            staffRegEnd=timezone.now() + timedelta(days=10),
            attendeeRegStart=timezone.now() - timedelta(days=10),
            attendeeRegEnd=timezone.now() + timedelta(days=10),
            onsiteRegStart=timezone.now() - timedelta(days=10),
            onsiteRegEnd=timezone.now() + timedelta(days=10),
            eventStart=timezone.now().date() + timedelta(days=30),
            eventEnd=timezone.now().date() + timedelta(days=33),
            collectBillingAddress=True,
            collectAddress=False,
        )

        # A paid tier with limited capacity
        self.paid_limited_tier = PriceLevel.objects.create(
            name="Paid Limited",
            description="A paid tier with limited capacity",
            basePrice=Decimal("1.00"),
            startDate=timezone.now() - timedelta(days=1),
            endDate=timezone.now() + timedelta(days=30),
            maxCapacity=2,
            capacityDisplayThreshold=5,
            public=True,
            available_to_attendee=True,
        )

        # A second tier so the UI shows the tier selection list
        self.free_tier = PriceLevel.objects.create(
            name="Free Tier",
            description="A free tier",
            basePrice=Decimal("0.00"),
            startDate=timezone.now() - timedelta(days=1),
            endDate=timezone.now() + timedelta(days=30),
            public=True,
            available_to_attendee=True,
        )

    def _fill_billing_form(self, page, suffix=""):
        """Fill the billing address form on the checkout page."""
        page.fill("#fname", f"Test{suffix}")
        page.fill("#lname", f"User{suffix}")
        page.fill("#email", f"billing{suffix}@example.com")
        page.fill("#add1", "123 Test Street")
        page.fill("#city", "Testville")

    def _fill_square_card_form(
        self, page, card_number=None, exp=None, cvv=None, postal=None
    ):
        """Fill the Square card form by interacting with its iframe.

        The Square Web Payments SDK renders card inputs inside a cross-origin
        iframe hosted on squarecdn.com. Playwright can access it via
        frame_locator().
        """
        if card_number is None:
            card_number = self.TEST_CARD_NUMBER
        if exp is None:
            exp = self.TEST_CARD_EXP
        if cvv is None:
            cvv = self.TEST_CARD_CVV
        if postal is None:
            postal = self.TEST_CARD_ZIP

        # Wait for the Square iframe to appear inside #card-container
        page.wait_for_selector(
            "#card-container iframe", state="attached", timeout=15000
        )

        # Access the card form iframe (titled "Secure Credit Card Form")
        card_frame = page.frame_locator('iframe[title="Secure Credit Card Form"]')

        # Fill each field inside the iframe
        card_frame.locator("#cardNumber").fill(card_number)
        card_frame.locator("#expirationDate").fill(exp)
        card_frame.locator("#cvv").fill(cvv)
        card_frame.locator("#postalCode").fill(postal)

    # ---- Test cases ----

    def test_paid_checkout_success(self):
        """Verify a paid checkout with real Square sandbox succeeds."""
        context, page = self._new_page()
        try:
            self._fill_registration_form(page, suffix="Paid1")
            self._select_tier_and_register(page, tier_name="Paid Limited")

            # On checkout page, fill billing and card form
            page.wait_for_selector("#card-container", state="visible", timeout=10000)
            self._fill_billing_form(page, suffix="Paid1")
            self._fill_square_card_form(page)

            page.wait_for_selector("#checkout", state="visible")
            page.click("#checkout")

            # Should redirect to done page on success
            page.wait_for_url("**/cart/done**", timeout=30000)
            self.assertIn("/cart/done", page.url)

            # Verify order was created in database
            order = Order.objects.filter(status=Order.COMPLETED).first()
            self.assertIsNotNone(order)
            self.assertEqual(order.total, Decimal("1.00"))
        finally:
            context.close()

    def test_paid_checkout_sold_out_shows_correct_error(self):
        """Verify paid checkout shows 'sold out' error, not 'mysterious reason'."""
        # Fill all capacity
        self._create_completed_order(self.paid_limited_tier, 1)

        context, page = self._new_page()
        try:
            self._fill_registration_form(page, suffix="PaidFail")
            self._select_tier_and_register(page, tier_name="Paid Limited")

            # Consume the last slot while user is on checkout page
            self._create_completed_order(self.paid_limited_tier, 1)

            # Fill billing and card, then attempt checkout
            page.wait_for_selector("#card-container", state="visible", timeout=10000)
            self._fill_billing_form(page, suffix="PaidFail")
            self._fill_square_card_form(page)
            page.wait_for_selector("#checkout", state="visible")
            page.click("#checkout")

            # Wait for error display
            status_container = page.locator("#payment-status-container")
            status_container.wait_for(state="visible", timeout=15000)
            error_text = status_container.text_content()

            # Should show meaningful capacity error, not "mysterious reason"
            self.assertIn("sold out", error_text.lower())
            self.assertNotIn("mysterious", error_text.lower())
            self.assertNotIn("unknown", error_text.lower())
        finally:
            context.close()

    def test_paid_checkout_card_declined_shows_payment_error(self):
        """Verify a declined card shows a payment error, not a capacity error."""
        context, page = self._new_page()
        try:
            self._fill_registration_form(page, suffix="Declined")
            self._select_tier_and_register(page, tier_name="Paid Limited")

            page.wait_for_selector("#card-container", state="visible", timeout=10000)
            self._fill_billing_form(page, suffix="Declined")
            self._fill_square_card_form(page, card_number=self.DECLINED_CARD_NUMBER)
            page.wait_for_selector("#checkout", state="visible")
            page.click("#checkout")

            # Wait for error display
            status_container = page.locator("#payment-status-container")
            status_container.wait_for(state="visible", timeout=15000)
            error_text = status_container.text_content()

            # Should show a payment error (Square returns error codes for declined cards)
            self.assertIn("payment", error_text.lower())
        finally:
            context.close()
