import uuid
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import override_settings

from registration.models import Cashdrawer, Firebase, HoldType
from registration.tests.common import (
    DEFAULT_EVENT_ARGS,
    Attendee,
    Badge,
    Client,
    Decimal,
    Event,
    Order,
    PriceLevel,
    PriceLevelOption,
    ShirtSizes,
    json,
    logging,
    now,
    one_day,
    reverse,
    ten_days,
)
from registration.views import onsite_admin


class OnsiteBaseTestCase(TestCase):
    def setUp(self):
        # Create some users
        self.admin_user = User.objects.create_superuser("admin", "admin@host", "admin")
        self.normal_user = User.objects.create_user("john", "lennon@thebeatles.com", "john")
        self.normal_user.staff_member = False
        self.normal_user.save()

        # Create some test terminals to push notifications to. Decision #8:
        # the bearer token is stored hash-only; mint it and keep the
        # plaintext for Bearer-auth assertions in this suite.
        self.terminal = Firebase(name="Terminal 1")
        self.terminal_token = self.terminal.mint_token()
        self.terminal.save()

        # At least one event always mandatory
        self.event = Event.objects.create(**DEFAULT_EVENT_ARGS)

        self.price_45 = PriceLevel.objects.create(
            name="Attendee",
            description="Some test description here",
            basePrice=45.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=True,
        )
        self.price_90 = PriceLevel.objects.create(
            name="Sponsor",
            description="Woot!",
            basePrice=90.00,
            startDate=now - ten_days,
            endDate=now + ten_days,
            public=True,
        )

        self.option_conbook = PriceLevelOption.objects.create(
            optionName="Conbook", optionPrice=0.00, optionExtraType="bool"
        )
        self.option_shirt = PriceLevelOption.objects.create(
            optionName="Shirt Size", optionPrice=20.00, optionExtraType="ShirtSizes"
        )
        self.option_100_int = PriceLevelOption.objects.create(
            optionName="Something Pricy", optionPrice=100.00, optionExtraType="int"
        )

        self.price_45.priceLevelOptions.set(
            [self.option_conbook, self.option_shirt, self.option_100_int]
        )
        self.price_90.priceLevelOptions.set(
            [self.option_conbook, self.option_shirt, self.option_100_int]
        )

        self.shirt1 = ShirtSizes.objects.create(name="Test_Large")

        self.boogeyman_hold = HoldType.objects.create(name="Boogeyman")

        self.client = Client()
        # MED-2: binding a terminal to the session now requires the
        # device's signed terminal-token cookie to match. The default
        # client "operates from" self.terminal; use bind_terminal() to
        # switch the proven terminal in a test.
        self.bind_terminal(self.terminal)

    def bind_terminal(self, terminal):
        """Set the signed terminal-token cookie proving possession of
        ``terminal`` (mirrors registration.views.onsite_admin)."""
        from django.core.signing import TimestampSigner

        self.client.cookies["terminal-token"] = TimestampSigner().sign_object(
            {"terminal": terminal.name}
        )

    def add_to_cart(self, level, options):
        form_data = {
            "attendee": {
                "address1": "Voluptas dolor dicta",
                "address2": "Debitis deleniti und",
                "asl": "",
                "badgeName": "Onsite Badge 1",
                "birthdate": "1989-02-07",
                "city": "64133",
                "country": "SK",
                "email": "apis@mailinator.com",
                "emailsOk": False,
                "firstName": "Cameron",
                "lastName": "Christian",
                "onsite": True,
                "phone": "+1 (143) 117-2402",
                "postal": "17416",
                "state": "KO",
                "surveyOk": False,
                "volDepts": "",
            },
            "event": self.event.name,
            "priceLevel": {"id": str(level.id), "options": options},
        }

        response = self.client.post(
            reverse("registration:add_to_cart"),
            json.dumps(form_data),
            content_type="application/json",
        )
        logging.info(response.content)
        self.assertEqual(response.status_code, 200)

    def checkout(self, charity_donation="0.00", org_donation="0.00"):
        post_data = {
            "processor": "paypal",
            "billingData": {
                "source_id": "TEST-PAYPAL-ORDER",
            },
            "charityDonation": charity_donation,
            "onsite": True,
            "orgDonation": org_donation,
        }

        response = self.client.post(
            reverse("registration:checkout"),
            json.dumps(post_data),
            content_type="application/json",
            headers={"idempotency-key": str(uuid.uuid4())},
        )

        return response


class TestOnsiteCart(OnsiteBaseTestCase):
    def setUp(self):
        super().setUp()

    def test_onsite_open(self):
        self.event.onsiteRegStart = now - one_day
        self.event.save()
        response = self.client.get(reverse("registration:onsite"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event"], self.event)
        self.assertIn(b"Onsite Registration", response.content)

    def test_onsite_closed_upcoming(self):
        self.event.onsiteRegStart = now + one_day
        self.event.save()
        response = self.client.get(reverse("registration:onsite"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event"], self.event)
        self.assertIn(b"not yet open", response.content)

    def test_onsite_closed_ended(self):
        self.event.onsiteRegEnd = now - one_day
        self.event.save()
        response = self.client.get(reverse("registration:onsite"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event"], self.event)
        self.assertIn(b"has ended", response.content)

    def test_onsite_checkout_cost(self):
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]
        self.add_to_cart(self.price_45, options)

        response = self.client.get(reverse("registration:onsite_cart"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.event, response.context["event"])
        self.assertEqual(
            self.price_45.basePrice + self.option_shirt.optionPrice,
            response.context["total"],
        )
        self.assertEqual(len(response.context["orderItems"]), 1)

        self.checkout()

    def test_onsite_checkout_free(self):
        pass

    def test_onsite_checkout_minor(self):
        pass

    def test_onsite_checkout_discount(self):
        pass

    def test_onsite_done(self):
        response = self.client.get(reverse("registration:onsite_done"))
        self.assertEqual(response.status_code, 200)


@override_settings(
    **{
        "MQTT_BROKER": {
            "host": "localhost",
            "port": 1883,
        },
        "MQTT_JWT_SECRET": "secret==",
        "MQTT_JWT_ALGORITHM": "HS256",
        "REGISTER_KEY": "",
        "REGISTER_PRINTER_URI": "",
    }
)
class TestOnsiteAdmin(OnsiteBaseTestCase):
    def test_onsite_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("registration:onsite_admin"), follow=True)
        self.assertRedirects(
            response,
            "/accounts/login/?next={}".format(reverse("registration:onsite_admin")),
        )

    def test_onsite_admin_required(self):
        self.client.logout()
        self.assertTrue(self.client.login(username="john", password="john"))
        response = self.client.get(reverse("registration:onsite_admin"), follow=True)
        self.assertContains(response, "403 Forbidden", status_code=403)
        self.client.logout()

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_admin(self, mock_send_mqtt_message):
        self.client.logout()
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.get(reverse("registration:onsite_admin"), follow=True)
        self.assertEqual(response.status_code, 200)

        self.terminal.delete()
        response = self.client.get(reverse("registration:onsite_admin"))
        self.assertEqual(response.status_code, 200)

        self.terminal = Firebase(name="Terminal 1")
        self.terminal_token = self.terminal.mint_token()
        self.terminal.save()

        response = self.client.get(
            reverse("registration:onsite_admin"),
            {"search": "Christian", "terminal": "1000"},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            reverse("registration:onsite_admin"),
            {"search": "Christian", "terminal": "notastring"},
        )
        self.assertEqual(response.status_code, 200)

        self.client.logout()

    def test_onsite_admin_search_no_query(self):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.get(
            reverse("registration:onsite_admin_search"),
        )
        self.assertEqual(response.status_code, 302)

    def test_onsite_admin_search_no_result(self):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.get(
            reverse("registration:onsite_admin_search"),
            {"search": "Somethingthatcantpossiblyexistyet"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 0)

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_admin_cart_no_donations(self, mock_send_mqtt_message):
        # Stage registration
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]
        self.add_to_cart(self.price_45, options)
        self.checkout()

        self.assertTrue(self.client.login(username="admin", password="admin"))

        # Do search
        response = self.client.get(
            reverse("registration:onsite_admin_search"),
            {"search": "Christian"},
        )
        self.assertEqual(response.status_code, 200)
        attendee = Badge.objects.get(pk=response.json()["results"][0]["id"]).attendee
        attendee.holdType = self.boogeyman_hold
        attendee.save()

        response = self.client.get(
            reverse("registration:onsite_admin_search"),
            {"search": "Christian", "terminal": self.terminal.id},
        )
        self.assertEqual(response.status_code, 200)

        # Add to cart
        badge_id = response.json()["results"][0]["id"]

        response = self.client.post(
            reverse("registration:onsite_add_to_cart") + f"?id={badge_id}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["cart"], [badge_id])

        response = self.client.get(reverse("registration:onsite_admin_cart"))
        message = response.json()

        self.assertEqual(message["result"][0]["holdType"], self.boogeyman_hold.name)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        self.assertEqual(
            float(message["total"]),
            float(self.price_45.basePrice + self.option_shirt.optionPrice),
        )

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_set_terminal_status(self, mock_send_mqtt_single):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.post(
            reverse("registration:terminal_status")
            + f"?{urlencode({'terminal': self.terminal.id, 'status': 'open'})}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mqtt_single.call_count, 1)

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_set_invalid_terminal_status(self, mock_send_mqtt_message):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.post(
            reverse("registration:terminal_status"),
            {"terminal": "notanint", "status": "open"},
        )

        message = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(message["success"])
        self.assertEqual(message["reason"], "No terminal associated with request")
        mock_send_mqtt_message.assert_not_called()

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_terminal_dne(self, mock_send_mqtt_message):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.post(
            reverse("registration:terminal_status"),
            {"terminal": 1000},
        )

        message = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(message["success"])
        self.assertEqual(
            message["reason"],
            "No terminal associated with request",
        )
        mock_send_mqtt_message.assert_not_called()

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_terminal_bad_request(self, mock_send_mqtt_message):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.post(
            reverse("registration:terminal_status"),
        )

        message = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(message["success"])
        self.assertEqual(message["reason"], "No terminal associated with request")
        mock_send_mqtt_message.assert_not_called()

    @patch("registration.mqtt.send_mqtt_message")
    def test_onsite_enabled_terminal(self, mock_send_mqtt_message):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        response = self.client.post(
            reverse("registration:enable_payment"),
            {"terminal": self.terminal.id},
        )
        self.assertEqual(response.status_code, 200)

    @patch("registration.mqtt.send_mqtt_message")
    def test_complete_cash_transaction(self, mock_send_mqtt_message):
        self.test_onsite_admin_cart_no_donations()
        self.client.get(reverse("registration:onsite_admin"), {"terminal": self.terminal.id})
        order = Order.objects.last()
        args = {
            "reference": order.reference,
            "total": order.total,
            "tendered": order.total,
        }
        response = self.client.post(
            reverse("registration:complete_cash_transaction") + f"?{urlencode(args)}"
        )
        self.assertEqual(response.status_code, 200)
        message = response.json()
        self.assertTrue(message["success"])
        order.refresh_from_db()
        self.assertEqual(order.billingType, Order.CASH)
        self.assertEqual(order.status, Order.COMPLETED)
        drawer = Cashdrawer.objects.last()
        self.assertEqual(drawer.total, order.total)

    @patch("registration.mqtt.send_mqtt_message")
    @patch("registration.payments.refresh_payment")
    def test_complete_square_transaction(self, mock_refresh_payment, mock_send_mqtt_message):
        mock_refresh_payment.return_value = (True, None)
        self.test_onsite_admin_cart_no_donations()
        order = Order.objects.last()
        args = {
            "reference": order.reference,
            "paymentId": "JUNK",
        }
        response = self.client.post(
            reverse("registration:complete_square_transaction"),
            json.dumps(args),
            content_type="application/json",
            headers={"authorization": f"Bearer {self.terminal_token}"},
        )
        self.assertEqual(response.status_code, 200)
        message = response.json()
        self.assertTrue(message["success"])
        order.refresh_from_db()
        self.assertEqual(order.billingType, Order.CREDIT)
        self.assertEqual(order.status, Order.COMPLETED)
        # Peer-review R3 (Test-Coverage): the CAS owns capacity on this
        # path — refresh_payment MUST be invoked with update_capacity=
        # False, else the BLOCK-3 double-decrement silently returns.
        mock_refresh_payment.assert_called_once_with(
            order, {"payment": {"id": "JUNK"}}, update_capacity=False
        )


@override_settings(
    MQTT_JWT_SECRET="secret==",
    MQTT_JWT_ALGORITHM="HS256",
    REGISTER_PRINTER_URI="",
)
class TestDrawers(OnsiteBaseTestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.client.login(username="admin", password="admin"))
        self.client.get(reverse("registration:onsite_admin"), {"terminal": self.terminal.id})

    def test_drawerStatusClosed_no_transactions(self):
        response = self.client.get(reverse("registration:drawer_status"))
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(message["success"])

    def test_drawerStatusClosed(self):
        Cashdrawer(total=100, action=Cashdrawer.OPEN).save()
        Cashdrawer(total=-100, action=Cashdrawer.CLOSE).save()
        response = self.client.get(reverse("registration:drawer_status"))
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        self.assertEqual(message["status"], "CLOSED")
        self.assertEqual(Decimal(message["total"]), Decimal("0.00"))

    def test_drawerStatusOpen(self):
        Cashdrawer(total=100, action=Cashdrawer.OPEN).save()
        response = self.client.get(reverse("registration:drawer_status"))
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        self.assertEqual(message["status"], "OPEN")
        self.assertEqual(Decimal(message["total"]), Decimal("100.00"))

    def test_drawerStatusShort(self):
        Cashdrawer(total=100, action=Cashdrawer.OPEN).save()
        Cashdrawer(total=-120, action=Cashdrawer.CLOSE).save()
        response = self.client.get(reverse("registration:drawer_status"))
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        self.assertEqual(message["status"], "SHORT")
        self.assertEqual(Decimal(message["total"]), Decimal("-20.00"))

    @patch("registration.mqtt.send_mqtt_message")
    def test_open_drawer(self, mock_send_mqtt_message):
        response = self.client.post(reverse("registration:open_drawer"), {"amount": "200"})
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        drawer = Cashdrawer.objects.last()
        self.assertEqual(drawer.action, Cashdrawer.OPEN)
        self.assertEqual(drawer.total, 200)
        mock_send_mqtt_message.assert_called_once()

    @patch("registration.mqtt.send_mqtt_message")
    def test_cash_deposit(self, mock_send_mqtt_message):
        response = self.client.post(reverse("registration:cash_deposit"), {"amount": "200"})
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        drawer = Cashdrawer.objects.last()
        self.assertEqual(drawer.action, Cashdrawer.DEPOSIT)
        self.assertEqual(drawer.total, 200)
        mock_send_mqtt_message.assert_called_once()

    @patch("registration.mqtt.send_mqtt_message")
    def test_safe_drop(self, mock_send_mqtt_message):
        response = self.client.post(reverse("registration:safe_drop"), {"amount": "200"})
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        drawer = Cashdrawer.objects.last()
        self.assertEqual(drawer.action, Cashdrawer.DROP)
        self.assertEqual(drawer.total, -200)
        mock_send_mqtt_message.assert_called_once()

    @patch("registration.mqtt.send_mqtt_message")
    def test_cash_pickup(self, mock_send_mqtt_message):
        response = self.client.post(reverse("registration:cash_pickup"), {"amount": "200"})
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        drawer = Cashdrawer.objects.last()
        self.assertEqual(drawer.action, Cashdrawer.PICKUP)
        self.assertEqual(drawer.total, -200)
        mock_send_mqtt_message.assert_called_once()

    @patch("registration.mqtt.send_mqtt_message")
    def test_close_drawer(self, mock_send_mqtt_message):
        response = self.client.post(reverse("registration:close_drawer"), {"amount": "200"})
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        drawer = Cashdrawer.objects.last()
        self.assertEqual(drawer.action, Cashdrawer.CLOSE)
        self.assertEqual(drawer.total, -200)
        mock_send_mqtt_message.assert_called_once()

    @patch("registration.mqtt.send_mqtt_message")
    def test_no_sale(self, mock_send_mqtt_message):
        response = self.client.post(reverse("registration:no_sale"))
        message = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(message["success"])
        mock_send_mqtt_message.assert_called_once()


class TestSearchFields(OnsiteBaseTestCase):
    def test_search_fields_parse(self):
        fields = onsite_admin.SearchFields.parse("")
        self.assertEqual(fields.query, "")
        self.assertIsNone(fields.birthday)
        self.assertIsNone(fields.badge_ids)

        fields = onsite_admin.SearchFields.parse(" test query ")
        self.assertEqual(fields.query, "test query")
        self.assertIsNone(fields.birthday)
        self.assertIsNone(fields.badge_ids)

        fields = onsite_admin.SearchFields.parse(" test query birthday:1990-01-01 ")
        self.assertEqual(fields.query, "test query")
        self.assertEqual(fields.birthday, "1990-01-01")
        self.assertIsNone(fields.badge_ids)

        fields = onsite_admin.SearchFields.parse("num:123,456")
        self.assertEqual(fields.query, "")
        self.assertIsNone(fields.birthday)
        self.assertEqual(fields.badge_ids, [123, 456])


class TestTerminalOrderBinding(OnsiteBaseTestCase):
    """S33 HIGH-1 + HIGH-2 (OWASP API1 BOLA).

    HIGH-1: attendee_details must scope its Badge lookup to the active
    onsite event and return a generic 404 across events (no cross-event
    PII oracle). HIGH-2: an order opened at one terminal must not be
    completable from another terminal, while legacy/null orders remain
    completable (fail-safe — the binding cannot break an existing
    checkout).
    """

    def setUp(self):
        super().setUp()
        # Second terminal with its own hash-only bearer token (Decision #8).
        self.terminal_b = Firebase(name="Terminal 2")
        self.terminal_b_token = self.terminal_b.mint_token()
        self.terminal_b.save()
        self.assertTrue(self.client.login(username="admin", password="admin"))

    def _make_badge(self, event):
        attendee = Attendee.objects.create(
            firstName="Jane",
            lastName="Doe",
            email=f"jane{Attendee.objects.count()}@example.com",
            birthdate=now.date() - ten_days,
        )
        return Badge.objects.create(
            attendee=attendee, event=event, badgeName=f"B{Badge.objects.count()}"
        )

    def _make_order(self, reference, opened_at=None):
        return Order.objects.create(
            total=Decimal("45.00"),
            reference=reference,
            status=Order.PENDING,
            billingType=Order.CREDIT,
            opened_at_terminal=opened_at,
        )

    # ---- HIGH-1: attendee_details event scoping ----

    def test_attendee_details_same_event_ok(self):
        badge = self._make_badge(self.event)
        response = self.client.get(
            reverse("registration:onsite_attendee_details"), {"id": badge.id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["attendee"]["firstName"], "Jane")

    def test_attendee_details_cross_event_404(self):
        other_event = Event.objects.create(
            **{**DEFAULT_EVENT_ARGS, "default": False, "name": "Other Event 2051!"}
        )
        badge = self._make_badge(other_event)
        response = self.client.get(
            reverse("registration:onsite_attendee_details"), {"id": badge.id}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["reason"], "Not found")

    # ---- HIGH-2: square (bearer-auth) terminal binding ----

    @patch("registration.mqtt.send_mqtt_message")
    @patch("registration.payments.refresh_payment")
    def _complete_square(self, reference, token, mock_refresh, _mock_mqtt):
        mock_refresh.return_value = (True, None)
        return self.client.post(
            reverse("registration:complete_square_transaction"),
            json.dumps({"reference": reference, "paymentId": "JUNK"}),
            content_type="application/json",
            headers={"authorization": f"Bearer {token}"},
        )

    def test_square_bound_order_blocks_other_terminal(self):
        order = self._make_order("BIND-A", opened_at=self.terminal)
        response = self._complete_square("BIND-A", self.terminal_b_token)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PENDING)

    def test_square_bound_order_allows_owning_terminal(self):
        self._make_order("BIND-OK", opened_at=self.terminal)
        response = self._complete_square("BIND-OK", self.terminal_token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    def test_square_legacy_null_order_still_completes(self):
        # Fail-safe invariant: an unbound (legacy) order completes from any
        # authenticated terminal, so the binding cannot break checkout.
        self._make_order("LEGACY", opened_at=None)
        response = self._complete_square("LEGACY", self.terminal_b_token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    # ---- HIGH-2: cash (session-terminal) binding ----

    @patch("registration.mqtt.send_mqtt_message")
    def test_cash_mismatch_rejected_when_both_known(self, _mock_mqtt):
        order = self._make_order("CASH-MM", opened_at=self.terminal)
        # Active session terminal = terminal_b (prove it via its cookie),
        # order bound to terminal A.
        self.bind_terminal(self.terminal_b)
        self.client.get(reverse("registration:onsite_admin"), {"terminal": self.terminal_b.id})
        args = {"reference": "CASH-MM", "total": order.total, "tendered": order.total}
        response = self.client.post(
            reverse("registration:complete_cash_transaction") + f"?{urlencode(args)}"
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PENDING)

    @patch("registration.mqtt.send_mqtt_message")
    def test_med2_url_param_without_proof_does_not_bind(self, _mock_mqtt):
        """MED-2: ?terminal= alone (cookie proves a different terminal)
        must NOT bind terminal_b. The order is opened@terminal_a, so with
        no proven terminal_b the cross-terminal guard cannot engage and
        the cash sale completes (proving the bind was rejected, not that
        terminal_b became the active terminal)."""
        order = self._make_order("CASH-NP", opened_at=self.terminal)
        # Cookie still proves self.terminal (base setUp); request binds
        # terminal_b by URL param only → rejected.
        self.client.get(reverse("registration:onsite_admin"), {"terminal": self.terminal_b.id})
        args = {"reference": "CASH-NP", "total": order.total, "tendered": order.total}
        response = self.client.post(
            reverse("registration:complete_cash_transaction") + f"?{urlencode(args)}"
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETED)

    @patch("registration.mqtt.send_mqtt_message")
    def test_cash_match_completes(self, _mock_mqtt):
        order = self._make_order("CASH-OK", opened_at=self.terminal)
        self.client.get(reverse("registration:onsite_admin"), {"terminal": self.terminal.id})
        args = {"reference": "CASH-OK", "total": order.total, "tendered": order.total}
        response = self.client.post(
            reverse("registration:complete_cash_transaction") + f"?{urlencode(args)}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETED)

    @patch("registration.mqtt.send_mqtt_message")
    def test_low4_cash_sale_is_audited(self, _mock_mqtt):
        """LOW-4 (S33-j): a completed cash sale emits a who/what/where/
        outcome audit line on fm_eventmanager.security."""
        order = self._make_order("CASH-AUD", opened_at=self.terminal)
        self.client.get(reverse("registration:onsite_admin"), {"terminal": self.terminal.id})
        args = {"reference": "CASH-AUD", "total": order.total, "tendered": order.total}
        with self.assertLogs("fm_eventmanager.security", level="WARNING") as cm:
            self.client.post(
                reverse("registration:complete_cash_transaction") + f"?{urlencode(args)}"
            )
        audit = [m for m in cm.output if "cash-drawer audit" in m]
        self.assertTrue(audit, "no cash-drawer audit line emitted")
        self.assertIn("action=cash_sale", audit[-1])
        self.assertIn("reference=CASH-AUD", audit[-1])
        self.assertIn("outcome=success", audit[-1])
