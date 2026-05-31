"""Regression guard: JS-context XSS (CWE-79 / OWASP A03).

S33 HIGH-3. Admin-set ``Event`` string values (``registrationEmail`` /
``dealerEmail`` / ``staffEmail`` — bare ``CharField`` with no validators —
and ``event.name``, reached directly or via ``str(event)`` = ``{{ event }}``)
were interpolated into ``<script>`` JS string literals (``alert("…")`` and
``'event': '{{ event }}'`` AJAX payloads). Django auto-escapes the HTML
context, not the JS-string context, so a crafted value could break out of
the string literal. The fix applies ``|escapejs`` at every such sink.

Two layers:

* :class:`NoUnescapedEventInScriptSourceTest` — a DB-free, deterministic
  *fixed-point* guard that scans every template's executable ``<script>``
  blocks (``type="application/json"`` ``json_script`` blocks excluded) and
  fails if any ``{{ event… }}`` / ``{{ attendee… }}`` lacks
  ``|escapejs``/``|json_script``,
  except the 3 explicitly-allowlisted numeric ``Discount.amountOff``
  sites (a ``DecimalField`` rendered as a canonical number and validated
  by the admin form — XSS-safe by type; ``|escapejs`` there would be
  wrong). This is what catches a future removal of a filter, reachable
  or not. It is a fixed-point guard for the *known* sink shape, not
  class-level recurrence prevention — the latter rests on the mandated
  CSP (no ``unsafe-inline``) + review.

* The view-rendering tests drive the affected templates through their
  real views with a poisoned ``Event`` and assert the security invariant
  at runtime: no breakout (raw or HTML-autoescaped) ever reaches an
  executable ``<script>`` block.
"""

import pathlib
import re

from django.test import SimpleTestCase
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
    now,
    ten_days,
)

# A value that, unescaped, breaks out of a double-/single-quoted JS string
# literal and injects script.
PAYLOAD = 'x");alert(1);//'
# Signatures of an UNescaped {{ }} reaching an executable script context:
# a raw double-quote breakout, or Django's HTML-autoescaped form (the
# project-defined defect when it lands inside a <script>). Both are
# legitimate elsewhere — a raw ``\");`` is how ``json_script`` safely
# serialises JSON, ``&quot;`` is correct HTML-context autoescaping
# (mailto:, <h1>) — so checks are scoped to executable (non
# application/json) <script> bodies only.
RAW_BREAKOUT = '");alert(1)'
HTML_ESCAPED_IN_JS = "&quot;);alert(1)"

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.S)
# Admin- or user-controlled string objects that reach JS string literals:
# ``event``/``str(event)`` (admin-set) and ``attendee`` fields
# (user-supplied — e.g. attendee.state/country in dealerasst-add.html).
_MODEL_VAR_RE = re.compile(r"\{\{\s*(?:event|attendee)\b[^}]*\}\}")
_TEMPLATES_ROOT = pathlib.Path(__file__).resolve().parent.parent / "templates"

# The only matched occurrences allowed inside an executable <script>
# without |escapejs/|json_script: numeric ``Discount.amountOff``
# (DecimalField, admin-validated, rendered as a canonical number — XSS-safe
# by type; |escapejs would corrupt the numeric literal).
_NUMERIC_ALLOWLIST = {
    ("registration/dealer/dealer-payment.html", "dealerDiscount"),
    ("registration/staff/new-staff-payment.html", "newStaffDiscount"),
    ("registration/staff/returning-staff-payment.html", "staffDiscount"),
}


def _poison(event):
    event.registrationEmail = PAYLOAD
    event.dealerEmail = PAYLOAD
    event.staffEmail = PAYLOAD
    event.name = PAYLOAD
    event.save()


def _executable_script_bodies(html):
    return [
        body for attrs, body in _SCRIPT_RE.findall(html) if "application/json" not in attrs.lower()
    ]


class NoUnescapedEventInScriptSourceTest(SimpleTestCase):
    """Deterministic, DB-free fixed-point guard over template source."""

    def test_no_unescaped_event_var_in_executable_script(self):
        offenders = []
        for path in sorted(_TEMPLATES_ROOT.rglob("*.html")):
            rel = path.relative_to(_TEMPLATES_ROOT).as_posix()
            src = path.read_text(encoding="utf-8", errors="replace")
            for attrs, body in _SCRIPT_RE.findall(src):
                if "application/json" in attrs.lower():
                    continue  # json_script — safe by construction
                for tag in _MODEL_VAR_RE.findall(body):
                    if "escapejs" in tag or "json_script" in tag:
                        continue
                    if any(rel == f and key in tag for f, key in _NUMERIC_ALLOWLIST):
                        continue  # numeric DecimalField — XSS-safe by type
                    offenders.append(f"{rel}: {tag.strip()}")
        self.assertEqual(
            offenders,
            [],
            "unescaped {{ event… }} in an executable <script> (add "
            "|escapejs, or |json_script for structured data):\n" + "\n".join(offenders),
        )


class XssAssertionMixin:
    def assert_no_script_breakout(self, response):
        """Security invariant: no breakout in any executable <script>."""
        self.assertEqual(response.status_code, 200)
        joined = "\n".join(_executable_script_bodies(response.content.decode()))
        self.assertNotIn(
            RAW_BREAKOUT,
            joined,
            "raw JS-string breakout in an executable <script> block",
        )
        self.assertNotIn(
            HTML_ESCAPED_IN_JS,
            joined,
            "unescaped {{ }} (HTML-autoescaped) reached an executable "
            "<script> block — needs |escapejs",
        )


class RegistrationFormXssTest(XssAssertionMixin, OrdersTestCase):
    def test_registration_form_event_email_escaped(self):
        _poison(self.event)
        response = self.client.get(reverse("registration:index"))
        self.assertTemplateUsed(response, "registration/registration-form.html")
        self.assert_no_script_breakout(response)

    def test_benign_email_not_mangled(self):
        self.event.registrationEmail = "events@example.org"
        self.event.save()
        response = self.client.get(reverse("registration:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "events@example.org")


class AttendeeLocateXssTest(XssAssertionMixin, OrdersTestCase):
    def test_attendee_locate_event_name_escaped(self):
        _poison(self.event)
        response = self.client.get(reverse("registration:upgrade", kwargs={"guid": "abc-123"}))
        self.assertTemplateUsed(response, "registration/attendee-locate.html")
        self.assert_no_script_breakout(response)


class OnsiteCheckoutXssTest(XssAssertionMixin, OrdersTestCase):
    def test_onsite_checkout_event_email_escaped(self):
        _poison(self.event)
        response = self.client.get(reverse("registration:onsite_cart"))
        self.assertTemplateUsed(response, "registration/onsite-checkout.html")
        self.assert_no_script_breakout(response)


class AttendeeUpgradeXssTest(XssAssertionMixin, OrdersTestCase):
    def setUp(self):
        super().setUp()
        self.attendee = Attendee(**TEST_ATTENDEE_ARGS)
        self.attendee.save()
        self.badge = Badge(attendee=self.attendee, event=self.event, badgeName="UX")
        self.badge.save()
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

    def test_attendee_upgrade_event_email_escaped(self):
        _poison(self.event)
        session = self.client.session
        session["attendee_id"] = self.attendee.pk
        session["badge_id"] = self.badge.pk
        session.save()
        response = self.client.get(reverse("registration:find_upgrade"))
        self.assertTemplateUsed(response, "registration/attendee-upgrade.html")
        self.assert_no_script_breakout(response)


class StaffPaymentXssTest(XssAssertionMixin, OrdersTestCase):
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

    def test_new_staff_payment_event_email_escaped(self):
        _poison(self.event)
        session = self.client.session
        session["new_staff"] = self.invite.token
        session.save()
        response = self.client.get(reverse("registration:info_new_staff"))
        self.assertTemplateUsed(response, "registration/staff/new-staff-payment.html")
        self.assert_no_script_breakout(response)

    def test_returning_staff_payment_event_email_escaped(self):
        _poison(self.event)
        response = self.client.get(reverse("registration:info_returning_staff"))
        self.assertTemplateUsed(response, "registration/staff/returning-staff-payment.html")
        self.assert_no_script_breakout(response)


class DealerXssTest(XssAssertionMixin, OrdersTestCase):
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

    def test_dealer_form_event_name_escaped(self):
        _poison(self.event)
        response = self.client.get(reverse("registration:new_dealer"))
        self.assertTemplateUsed(response, "registration/dealer/dealer-form.html")
        self.assert_no_script_breakout(response)

    def test_dealer_payment_event_email_escaped(self):
        _poison(self.event)
        session = self.client.session
        session["dealer_id"] = self.dealer.pk
        session.save()
        response = self.client.get(reverse("registration:info_dealer"))
        self.assertTemplateUsed(response, "registration/dealer/dealer-payment.html")
        self.assert_no_script_breakout(response)
