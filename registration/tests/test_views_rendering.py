"""Exhaustive front-end rendering coverage for every HTML-returning URL in
``registration/urls.py``.

Scope
-----

For each HTML page reachable via the Django URL conf:

* Plain GET returns ``200`` and uses the expected template.
* Key user-visible strings / form fields / context variables are present.
* Conditional branches (event open / not-yet / ended) all render.
* Session-dependent views render both with and without the required session
  keys so both branches are exercised.

For JSON endpoints driven from front-end JS (checkout, cart, info_upgrade,
find_*): minimal validation/branch coverage — full happy-path backend
behavior is already covered by the existing ``test_master``,
``test_paypal_checkout``, ``test_dealers``, ``test_staff``,
``test_upgrades`` suites. The point of this file is to hit template
rendering and JSON boundary code paths for coverage, not to re-exercise the
checkout state machine.

PayPal-only path
----------------

Tests that interact with payment selection drive the PayPal flow exclusively
per project policy. Square code is not exercised or modified here.

Reuses: :class:`registration.tests.common.OrdersTestCase` and
:class:`registration.tests.common.PayPalOrdersTestCase` for fixtures.
"""

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from registration.models import (
    Attendee,
    Badge,
    Dealer,
    Order,
    OrderItem,
    Staff,
    StaffInvite,
    Venue,
)
from registration.tests.common import (
    DEFAULT_VENUE_ARGS,
    TEST_ATTENDEE_ARGS,
    OrdersTestCase,
    PayPalOrdersTestCase,
    now,
    one_day,
    ten_days,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_staff_user():
    """Return a saved superuser suitable for staff-only views."""
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin-" + uuid.uuid4().hex[:8],
        email="admin@example.com",
        password="adminpass123",
    )


def _shift_event_window(event, *, start_delta, end_delta, fields):
    """Shift the given reg-window fields on ``event`` by the given deltas.

    ``fields`` is a sequence of ``(start_attr, end_attr)`` tuples. Used to
    flip events into "not yet open" / "closed" states so the corresponding
    template branches render.
    """
    for start_attr, end_attr in fields:
        setattr(event, start_attr, now + start_delta)
        setattr(event, end_attr, now + end_delta)
    event.save()


# ---------------------------------------------------------------------------
# Top-level / utility views
# ---------------------------------------------------------------------------


class IndexRenderingTest(OrdersTestCase):
    def test_index_open_renders_registration_form(self):
        response = self.client.get(reverse("registration:index"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/registration-form.html")
        self.assertEqual(response.context["form_type"], "attendee")
        self.assertEqual(response.context["event"].pk, self.event.pk)

    def test_index_not_yet_open(self):
        _shift_event_window(
            self.event,
            start_delta=one_day,
            end_delta=ten_days,
            fields=[("attendeeRegStart", "attendeeRegEnd")],
        )
        response = self.client.get(reverse("registration:index"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/closed.html")
        self.assertContains(response, "not yet open")

    def test_index_closed_ended(self):
        _shift_event_window(
            self.event,
            start_delta=-ten_days,
            end_delta=-one_day,
            fields=[("attendeeRegStart", "attendeeRegEnd")],
        )
        response = self.client.get(reverse("registration:index"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/closed.html")
        self.assertContains(response, "has ended")

    def test_index_home_redirect_uses_website_url(self):
        _shift_event_window(
            self.event,
            start_delta=one_day,
            end_delta=ten_days,
            fields=[("attendeeRegStart", "attendeeRegEnd")],
        )
        self.event.websiteUrl = "https://example.com"
        self.event.save()
        response = self.client.get(reverse("registration:index"))
        self.assertContains(response, "https://example.com")


class NoEventIndexTest(TestCase):
    """Index renders the no-event template when no default event exists."""

    def test_no_default_event(self):
        response = self.client.get(reverse("registration:index"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/docs/no-event.html")
        self.assertContains(response, "no default event was found")


# ---------------------------------------------------------------------------
# Onsite storefront
# ---------------------------------------------------------------------------


class OnsiteRenderingTest(OrdersTestCase):
    def test_onsite_open(self):
        response = self.client.get(reverse("registration:onsite"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/onsite.html")
        self.assertEqual(response.context["form_type"], "attendee")

    def test_onsite_not_yet_open(self):
        _shift_event_window(
            self.event,
            start_delta=one_day,
            end_delta=ten_days,
            fields=[("onsiteRegStart", "onsiteRegEnd")],
        )
        response = self.client.get(reverse("registration:onsite"))
        self.assertTemplateUsed(response, "registration/closed.html")
        self.assertContains(response, "not yet open")

    def test_onsite_ended(self):
        _shift_event_window(
            self.event,
            start_delta=-ten_days,
            end_delta=-one_day,
            fields=[("onsiteRegStart", "onsiteRegEnd")],
        )
        response = self.client.get(reverse("registration:onsite"))
        self.assertTemplateUsed(response, "registration/closed.html")
        self.assertContains(response, "has ended")

    def test_onsite_cart_empty_renders(self):
        response = self.client.get(reverse("registration:onsite_cart"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/onsite-checkout.html")
        self.assertEqual(response.context["orderItems"], [])

    def test_onsite_done(self):
        response = self.client.get(reverse("registration:onsite_done"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/onsite-done.html")


# ---------------------------------------------------------------------------
# Cart / checkout rendering
# ---------------------------------------------------------------------------


class CartRenderingTest(OrdersTestCase):
    def test_cart_empty_renders_checkout(self):
        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/checkout.html")
        self.assertEqual(response.context["orderItems"], [])
        self.assertEqual(response.context["total"], 0)
        # PayPal preservation: client-side JS pulls this header to open the
        # PayPal popup without SameOrigin complaints.
        self.assertEqual(
            response.headers.get("Cross-Origin-Opener-Policy"),
            "same-origin-allow-popups",
        )

    def test_cart_with_session_items_renders_items(self):
        self.add_to_cart(self.attendee_form_1, self.price_45, [])
        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/checkout.html")
        self.assertTrue(len(response.context["orderItems"]) >= 1)

    def test_cart_done(self):
        response = self.client.get(reverse("registration:done"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/done.html")


# ---------------------------------------------------------------------------
# JSON endpoints for the cart (coverage of validation branches)
# ---------------------------------------------------------------------------


class CartJsonEndpointsTest(OrdersTestCase):
    def test_add_to_cart_bad_json(self):
        response = self.client.post(
            reverse("registration:add_to_cart"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])

    def test_remove_from_cart_bad_json(self):
        response = self.client.post(
            reverse("registration:remove_from_cart"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_remove_from_cart_missing_id(self):
        response = self.client.post(
            reverse("registration:remove_from_cart"),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("id", response.json()["reason"])

    def test_remove_from_cart_unknown_id(self):
        response = self.client.post(
            reverse("registration:remove_from_cart"),
            data=json.dumps({"id": 999999}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_remove_from_cart_success(self):
        self.add_to_cart(self.attendee_form_1, self.price_45, [])
        session = self.client.session
        cart_ids = session["cart_items"]
        response = self.client.post(
            reverse("registration:remove_from_cart"),
            data=json.dumps({"id": cart_ids[0]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_flush_clears_session(self):
        self.add_to_cart(self.attendee_form_1, self.price_45, [])
        response = self.client.post(reverse("registration:flush"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    @tag("paypal")
    def test_paypalcreate_requires_json_body(self):
        response = self.client.post(
            reverse("registration:paypalcreate"),
            data="not json",
            content_type="application/json",
        )
        # empty cart or bad json both short-circuit before the SDK is hit.
        self.assertIn(response.status_code, (400, 500))


# ---------------------------------------------------------------------------
# Simple JSON lookup endpoints (events, departments, shirts, tables, addresses)
# ---------------------------------------------------------------------------


class JsonLookupEndpointsTest(OrdersTestCase):
    def test_get_events_returns_array(self):
        response = self.client.get(reverse("registration:events"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIsInstance(data, list)
        self.assertTrue(any(e["id"] == self.event.pk for e in data))

    def test_get_departments(self):
        response = self.client.get(reverse("registration:departments"))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(json.loads(response.content), list)

    def test_get_all_departments(self):
        response = self.client.get(reverse("registration:alldepartments"))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(json.loads(response.content), list)

    def test_get_shirt_sizes(self):
        response = self.client.get(reverse("registration:shirtsizes"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(any(s["name"] == "Test_Large" for s in data))

    def test_get_price_levels_rejects_get(self):
        """Endpoint is POST-only; GET returns a 400 error envelope."""
        response = self.client.get(reverse("registration:pricelevels"))
        self.assertEqual(response.status_code, 400)

    def test_get_price_levels_requires_valid_birthdate(self):
        response = self.client.post(
            reverse("registration:pricelevels"),
            data=json.dumps({"form_type": "attendee"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_price_levels_attendee(self):
        response = self.client.post(
            reverse("registration:pricelevels"),
            data=json.dumps(
                {"year": 1990, "month": 1, "day": 1, "form_type": "attendee"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in json.loads(response.content)]
        # The public price levels from OrdersTestCase should be visible.
        self.assertIn("Attendee", names)
        self.assertIn("Sponsor", names)

    def test_get_price_levels_bad_json(self):
        response = self.client.post(
            reverse("registration:pricelevels"),
            data="nope",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_table_sizes(self):
        response = self.client.get(reverse("registration:tablesizes"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIsInstance(data, list)

    def test_get_session_addresses_empty(self):
        response = self.client.get(reverse("registration:addresses"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"{}")


# ---------------------------------------------------------------------------
# Upgrade flow
# ---------------------------------------------------------------------------


class UpgradeRenderingTest(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.attendee = Attendee(**TEST_ATTENDEE_ARGS)
        self.attendee.save()
        self.badge = Badge(attendee=self.attendee, event=self.event, badgeName="UX")
        self.badge.save()
        # Give the badge a real OrderItem attached to an Order so
        # effectiveLevel() (which filters ``order__isnull=False``) returns
        # a level.
        self.existing_order = Order.objects.create(
            total="45.00",
            status=Order.COMPLETED,
            reference="UX-PRIOR",
            billingEmail="apis@mailinator.com",
        )
        self.order_item = OrderItem(
            badge=self.badge,
            priceLevel=self.price_45,
            order=self.existing_order,
            enteredBy="WEB",
        )
        self.order_item.save()

    def test_upgrade_landing_page(self):
        response = self.client.get(
            reverse("registration:upgrade", kwargs={"guid": "abc-123"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/attendee-locate.html")
        self.assertEqual(response.context["token"], "abc-123")

    def test_find_upgrade_without_session_400(self):
        response = self.client.get(reverse("registration:find_upgrade"))
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "registration/attendee-upgrade.html")

    def test_find_upgrade_with_session_renders_attendee(self):
        session = self.client.session
        session["attendee_id"] = self.attendee.pk
        session["badge_id"] = self.badge.pk
        session.save()
        response = self.client.get(reverse("registration:find_upgrade"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/attendee-upgrade.html")
        self.assertEqual(response.context["attendee"].pk, self.attendee.pk)

    def test_invoice_upgrade_empty_session(self):
        response = self.client.get(reverse("registration:invoice_upgrade"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/upgrade-checkout.html")
        self.assertEqual(response.context["total"], 0)

    def test_invoice_upgrade_with_items(self):
        session = self.client.session
        session["attendee_id"] = self.attendee.pk
        session["badge_id"] = self.badge.pk
        upgrade_item = OrderItem(
            badge=self.badge, priceLevel=self.price_90, enteredBy="WEB"
        )
        upgrade_item.save()
        session["order_items"] = [upgrade_item.pk]
        session.save()
        response = self.client.get(reverse("registration:invoice_upgrade"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/upgrade-checkout.html")

    def test_done_upgrade(self):
        response = self.client.get(reverse("registration:done_upgrade"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/upgrade-done.html")

    def test_info_upgrade_bad_json(self):
        response = self.client.post(
            reverse("registration:info_upgrade"),
            data="nope",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_info_upgrade_missing_fields(self):
        response = self.client.post(
            reverse("registration:info_upgrade"),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_info_upgrade_wrong_email(self):
        self.badge.registrationToken = "tok-123"
        self.badge.save()
        response = self.client.post(
            reverse("registration:info_upgrade"),
            data=json.dumps({"email": "wrong@example.com", "token": "tok-123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_info_upgrade_success(self):
        self.badge.registrationToken = "tok-456"
        self.badge.save()
        response = self.client.post(
            reverse("registration:info_upgrade"),
            data=json.dumps({"email": self.attendee.email, "token": "tok-456"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])


# ---------------------------------------------------------------------------
# Staff flow
# ---------------------------------------------------------------------------


class StaffRenderingTest(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.attendee = Attendee(**TEST_ATTENDEE_ARGS)
        self.attendee.save()
        self.staff = Staff(attendee=self.attendee, event=self.event)
        self.staff.save()
        self.invite = StaffInvite.objects.create(
            email=self.attendee.email,
            token="inv-token-123",
            validUntil=now + ten_days,
        )

    def test_new_staff_open(self):
        response = self.client.get(
            reverse("registration:new_staff", kwargs={"guid": self.invite.token})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/staff/new-staff.html")
        self.assertEqual(response.context["form_type"], "staff")

    def test_new_staff_not_yet_open(self):
        _shift_event_window(
            self.event,
            start_delta=one_day,
            end_delta=ten_days,
            fields=[("staffRegStart", "staffRegEnd")],
        )
        response = self.client.get(
            reverse("registration:new_staff", kwargs={"guid": self.invite.token})
        )
        self.assertTemplateUsed(response, "registration/staff/staff-closed.html")

    def test_new_staff_ended(self):
        _shift_event_window(
            self.event,
            start_delta=-ten_days,
            end_delta=-one_day,
            fields=[("staffRegStart", "staffRegEnd")],
        )
        response = self.client.get(
            reverse("registration:new_staff", kwargs={"guid": self.invite.token})
        )
        self.assertTemplateUsed(response, "registration/staff/staff-closed.html")

    def test_info_new_staff_without_session(self):
        response = self.client.get(reverse("registration:info_new_staff"))
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "registration/staff/new-staff-payment.html")

    def test_info_new_staff_with_valid_session(self):
        session = self.client.session
        session["new_staff"] = self.invite.token
        session.save()
        response = self.client.get(reverse("registration:info_new_staff"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/staff/new-staff-payment.html")

    def test_returning_staff_landing(self):
        response = self.client.get(
            reverse("registration:returning_staff", kwargs={"guid": "g"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/staff/returning-staff.html")

    def test_info_returning_staff_no_session(self):
        response = self.client.get(reverse("registration:info_returning_staff"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "registration/staff/returning-staff-payment.html"
        )

    def test_info_returning_staff_with_session(self):
        session = self.client.session
        session["staff_id"] = self.staff.pk
        session.save()
        response = self.client.get(reverse("registration:info_returning_staff"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "registration/staff/returning-staff-payment.html"
        )

    def test_staff_done(self):
        for url_name in ("new_staff_done", "returning_staff_done"):
            response = self.client.get(reverse(f"registration:{url_name}"))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "registration/staff/staff-done.html")

    def test_find_new_staff_bad_json(self):
        response = self.client.post(
            reverse("registration:find_new_staff"),
            data="bad",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_find_new_staff_not_found(self):
        response = self.client.post(
            reverse("registration:find_new_staff"),
            data=json.dumps({"email": "nobody@example.com", "token": "zz"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_find_new_staff_success(self):
        response = self.client.post(
            reverse("registration:find_new_staff"),
            data=json.dumps({"email": self.attendee.email, "token": self.invite.token}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_find_returning_staff_bad_json(self):
        response = self.client.post(
            reverse("registration:find_returning_staff"),
            data="bad",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_find_returning_staff_missing(self):
        response = self.client.post(
            reverse("registration:find_returning_staff"),
            data=json.dumps({"email": "x", "token": "y"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Dealer flow
# ---------------------------------------------------------------------------


class DealerRenderingTest(OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.venue = Venue.objects.create(**DEFAULT_VENUE_ARGS)
        self.event.venue = self.venue
        self.event.save()
        self.attendee = Attendee(**TEST_ATTENDEE_ARGS)
        self.attendee.save()
        self.dealer = Dealer.objects.create(
            attendee=self.attendee,
            event=self.event,
            tableSize=self.table_130,
            businessName="Test Shop",
            website="",
            description="",
            license="",
        )

    def test_new_dealer_open(self):
        response = self.client.get(reverse("registration:new_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-form.html")
        self.assertEqual(response.context["form_type"], "marketplace")

    def test_new_dealer_not_yet_open(self):
        _shift_event_window(
            self.event,
            start_delta=one_day,
            end_delta=ten_days,
            fields=[("dealerRegStart", "dealerRegEnd")],
        )
        response = self.client.get(reverse("registration:new_dealer"))
        self.assertTemplateUsed(response, "registration/dealer/dealer-closed.html")

    def test_new_dealer_ended(self):
        _shift_event_window(
            self.event,
            start_delta=-ten_days,
            end_delta=-one_day,
            fields=[("dealerRegStart", "dealerRegEnd")],
        )
        response = self.client.get(reverse("registration:new_dealer"))
        self.assertTemplateUsed(response, "registration/dealer/dealer-closed.html")

    def test_dealers_landing(self):
        response = self.client.get(
            reverse("registration:dealers", kwargs={"guid": "abc"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-locate.html")

    def test_thanks_dealer(self):
        response = self.client.get(reverse("registration:thanks_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-thanks.html")

    def test_done_dealer(self):
        response = self.client.get(reverse("registration:done_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-done.html")

    def test_info_dealer_no_session(self):
        response = self.client.get(reverse("registration:info_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-payment.html")

    def test_info_dealer_with_session(self):
        session = self.client.session
        session["dealer_id"] = self.dealer.pk
        session.save()
        response = self.client.get(reverse("registration:info_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-payment.html")
        self.assertEqual(response.context["dealer"].pk, self.dealer.pk)

    def test_invoice_dealer_no_session(self):
        response = self.client.get(reverse("registration:invoice_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealer-checkout.html")

    def test_add_assistants_no_session(self):
        response = self.client.get(reverse("registration:add_assistants"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealerasst-add.html")

    def test_add_assistants_with_session(self):
        session = self.client.session
        session["dealer_id"] = self.dealer.pk
        session.save()
        response = self.client.get(reverse("registration:add_assistants"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealerasst-add.html")
        self.assertEqual(response.context["dealer"].pk, self.dealer.pk)

    def test_find_dealer_to_add_assistant(self):
        response = self.client.get(
            reverse(
                "registration:find_dealer_to_add_assistant",
                kwargs={"guid": "x"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealerasst-locate.html")

    def test_dealer_asst(self):
        response = self.client.get(
            reverse("registration:dealer_asst", kwargs={"guid": "x"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealerasst-locate.html")

    def test_done_asst_dealer(self):
        response = self.client.get(reverse("registration:done_asst_dealer"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/dealer/dealerasst-done.html")

    def test_find_dealer_missing(self):
        response = self.client.post(
            reverse("registration:find_dealer"),
            data=json.dumps({"email": "x@example.com", "token": "none"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_find_dealer_to_add_assistant_post_bad_body(self):
        response = self.client.post(
            reverse("registration:find_dealer_to_add_assistant_post"),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------------------
# Staff-only utility pages
# ---------------------------------------------------------------------------


@tag("paypal")
class UtilityPagesStaffRequiredTest(PayPalOrdersTestCase):
    """``basicBadges`` and ``vipBadges`` are ``@staff_member_required`` —
    they redirect unauthenticated users and render the list for staff."""

    def setUp(self):
        super().setUp()
        self.staff_user = _make_staff_user()

    def test_basic_badges_requires_staff(self):
        response = self.client.get(reverse("registration:basicBadges"))
        self.assertEqual(response.status_code, 302)

    def test_basic_badges_renders_for_staff(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("registration:basicBadges"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/utility/badgelist.html")
        self.assertIn("attendees", response.context)
        self.assertIn("staff", response.context)

    def test_vip_badges_requires_staff(self):
        response = self.client.get(reverse("registration:vipBadges"))
        self.assertEqual(response.status_code, 302)

    def test_vip_badges_renders_for_staff(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("registration:vipBadges"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/utility/viplist.html")


# ---------------------------------------------------------------------------
# Onsite admin SPA host + JSON endpoints
# ---------------------------------------------------------------------------


class OnsiteAdminAccessTest(OrdersTestCase):
    """Entry points to the onsite admin. The SPA itself is covered by
    Vitest; here we only verify the host page renders and that
    ``@staff_member_required`` gates unauthenticated access."""

    def setUp(self):
        super().setUp()
        self.staff_user = _make_staff_user()

    def test_onsite_admin_requires_staff(self):
        response = self.client.get(reverse("registration:onsite_admin"))
        self.assertEqual(response.status_code, 302)

    def test_onsite_admin_renders_spa_host(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("registration:onsite_admin"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/spa-host.html")

    def test_onsite_admin_terminals_json(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("registration:onsite_admin_terminals"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("terminals", response.json())

    def test_onsite_admin_context_json(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("registration:onsite_admin_context"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("user", body)
        self.assertIn("permissions", body)
        self.assertIn("shirtSizes", body)
        self.assertIn("departments", body)
