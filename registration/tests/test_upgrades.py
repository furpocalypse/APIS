import json
import unittest
from unittest.mock import patch

from django.test import tag
from django.urls import reverse

from registration.models import *
from registration.tests.common import OrdersTestCase


class TestUpgrades(OrdersTestCase):
    def test_upgrade_index(self):
        guid = "ARSTBCESKFGHAIESTRK"
        response = self.client.get(reverse("registration:upgrade", args=[guid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, guid)

    def test_infoUpgrade_bad_json(self):
        response = self.client.post(
            reverse("registration:info_upgrade"),
            "notJSON-",
            content_type="application/json",
        )
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(data, {"success": False})

    def test_infoUpgrade_wrong_token(self):
        # Failed lookup against info_upgrade()
        attendee = Attendee(**self.attendee_upgrade)
        attendee.save()
        badge = Badge(attendee=attendee, event=self.event, badgeName="Test Upgrade")
        badge.save()
        post_data = {
            "email": attendee.email,
            "token": "notTheRightToken",
            "event": self.event.name,
        }
        response = self.client.post(
            reverse("registration:info_upgrade"),
            json.dumps(post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        badge.delete()
        attendee.delete()

    def test_infoUpgrade_wrong_email(self):
        # Failed lookup against info_upgrade()
        attendee = Attendee(**self.attendee_upgrade)
        attendee.save()
        badge = Badge(attendee=attendee, event=self.event, badgeName="Test Upgrade")
        badge.save()
        post_data = {
            "email": "nottherightemail@somewhere.com",
            "token": badge.registrationToken,
        }
        response = self.client.post(
            reverse("registration:info_upgrade"),
            json.dumps(post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        badge.delete()
        attendee.delete()

    def test_infoUpgrade_happy_path(self):
        attendee = Attendee(**self.attendee_upgrade)
        attendee.save()
        badge = Badge(attendee=attendee, event=self.event, badgeName="Test Upgrade")
        badge.save()
        post_data = {
            "event": self.event.name,
            "email": badge.attendee.email,
            "token": badge.registrationToken,
        }
        response = self.client.post(
            reverse("registration:info_upgrade"),
            json.dumps(post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        badge.delete()
        attendee.delete()

    @tag("square")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_findUpgrade_happy_path(self, mock_capture):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]
        self.add_to_cart(self.attendee_form_upgrade, self.price_45, options)

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        response = self.checkout("cnon:card-nonce-ok", "0", "0")
        self.assertEqual(response.status_code, 200)

        # Check that user was successfully saved
        attendee = Attendee.objects.get(firstName="Upgrade", lastName="Me")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertEqual(badge.effectiveLevel(), self.price_45)

        post_data = {
            "event": self.event.name,
            "email": badge.attendee.email,
            "token": badge.registrationToken,
        }
        response = self.client.post(
            reverse("registration:info_upgrade"),
            json.dumps(post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("registration:find_upgrade"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["attendee"], attendee)
        self.assertEqual(response.context["badge"], badge)

        badge.delete()
        attendee.delete()

    @tag("square")
    def setup_upgrade(self):
        # set up existing registration
        options = [
            {"id": self.option_conbook.id, "value": "true"},
            {"id": self.option_shirt.id, "value": self.shirt1.id},
        ]
        self.add_to_cart(self.attendee_form_upgrade, self.price_45, options)

        response = self.client.get(reverse("registration:cart"))
        self.assertEqual(response.status_code, 200)
        cart = response.context["orderItems"]
        self.assertEqual(len(cart), 1)
        total = response.context["total"]
        self.assertEqual(total, 45)

        response = self.checkout("cnon:card-nonce-ok", "0", "0")
        self.assertEqual(response.status_code, 200)

        # Check that user was successfully saved
        attendee = Attendee.objects.get(firstName="Upgrade", lastName="Me")
        badge = Badge.objects.get(attendee=attendee, event=self.event)
        self.assertEqual(badge.effectiveLevel(), self.price_45)

        # info_upgrade()
        post_data = {
            "event": self.event.name,
            "email": badge.attendee.email,
            "token": badge.registrationToken,
        }
        response = self.client.post(
            reverse("registration:info_upgrade"),
            json.dumps(post_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return badge, attendee

    def upgrade_add_and_checkout(self, price_level, form, badge, attendee):
        # add_upgrade()
        post_data = {
            "event": self.event.name,
            "badge": {
                "id": badge.id,
            },
            "attendee": {
                "id": attendee.id,
            },
            "priceLevel": {
                "id": price_level.id,
                "options": [],
            },
        }
        self.client.post(
            reverse("registration:add_upgrade"),
            json.dumps(post_data),
            content_type="application/json",
        )
        cart_response = self.client.get(reverse("registration:invoice_upgrade"))

        checkout_response = self.client.post(
            reverse("registration:checkout_upgrade"),
            json.dumps(form),
            content_type="application/json",
        )
        return cart_response, checkout_response

    @tag("square")
    @unittest.skip(
        "Square upgrade checkout path unwired; see test_upgrade_checkout_via_paypal for the PayPal equivalent."
    )
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_upgrade(self, mock_capture):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        badge, attendee = self.setup_upgrade()
        cart, checkout = self.upgrade_add_and_checkout(
            self.price_90, self.attendee_form_upgrade_checkout, badge, attendee
        )
        self.assertEqual(cart.status_code, 200)
        self.assertEqual(cart.context["total"], self.price_45.basePrice)
        self.assertEqual(cart.context["total_discount"], 0)
        self.assertEqual(cart.status_code, 200)
        self.assertEqual(badge.effectiveLevel(), self.price_90)
        badge.delete()
        attendee.delete()

    @tag("square")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_upgrade_zero(self, mock_capture):
        mock_capture.return_value = (
            True,
            {"id": "TEST-PAYPAL-ORDER", "status": "COMPLETED"},
        )
        badge, attendee = self.setup_upgrade()
        cart, checkout = self.upgrade_add_and_checkout(
            self.price_45, self.attendee_form_upgrade_checkout, badge, attendee
        )
        self.assertEqual(checkout.status_code, 200)
        self.assertEqual(badge.effectiveLevel(), self.price_45)
        badge.delete()
        attendee.delete()

    @tag("square")
    @unittest.skip("Square checkout removed; rewrite against PayPal capture failure path")
    def test_upgrade_card_declined(self):
        form = self.attendee_form_upgrade_checkout
        form["nonce"] = "cnon:card-nonce-declined"
        badge, attendee = self.setup_upgrade()
        cart, checkout = self.upgrade_add_and_checkout(self.price_45, form, badge, attendee)
        self.assertEqual(checkout.status_code, 200)
        self.assertEqual(badge.effectiveLevel(), self.price_45)
        badge.delete()
        attendee.delete()

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_upgrade_checkout_via_paypal(self, mock_capture):
        """End-to-end upgrade checkout via the PayPal path (capture mocked)."""

        def fake_capture(paypal_order_id, apis_order):
            apis_order.status = Order.COMPLETED
            apis_order.apiData = {"id": paypal_order_id, "status": "COMPLETED"}
            return True, {"id": paypal_order_id, "status": "COMPLETED"}

        mock_capture.side_effect = fake_capture
        badge, attendee = self.setup_upgrade()
        setup_call_count = mock_capture.call_count

        form = dict(self.attendee_form_upgrade_checkout)
        form["orderID"] = "TEST-PAYPAL-UPGRADE-ORDER"
        cart, checkout = self.upgrade_add_and_checkout(self.price_90, form, badge, attendee)
        self.assertEqual(cart.status_code, 200)
        self.assertEqual(checkout.status_code, 200, checkout.content)
        self.assertEqual(mock_capture.call_count - setup_call_count, 1)
        self.assertEqual(mock_capture.call_args.args[0], "TEST-PAYPAL-UPGRADE-ORDER")

        badge.refresh_from_db()
        self.assertEqual(badge.effectiveLevel(), self.price_90)

        upgrade_item = (
            OrderItem.objects.filter(badge=badge, priceLevel=self.price_90)
            .exclude(order__isnull=True)
            .first()
        )
        self.assertIsNotNone(upgrade_item)
        order = upgrade_item.order
        self.assertEqual(order.billingType, Order.CREDIT)
        self.assertEqual(order.status, Order.COMPLETED)
        # price_90 ($90) - price_45 ($45) + orgDonation ($10) = $55
        self.assertEqual(order.total, Decimal("55.00"))
        badge.delete()
        attendee.delete()

    @tag("paypal")
    @patch("registration.views.ordering.capture_paypal_payment")
    def test_upgrade_paypal_capture_declined(self, mock_capture):
        """PayPal variant of test_upgrade_card_declined (Square version is skipped)."""

        def fake_success(paypal_order_id, apis_order):
            apis_order.status = Order.COMPLETED
            apis_order.apiData = {"id": paypal_order_id, "status": "COMPLETED"}
            return True, {"id": paypal_order_id, "status": "COMPLETED"}

        def fake_failed_capture(paypal_order_id, apis_order):
            apis_order.status = Order.FAILED
            apis_order.save()
            return False, {"errors": ["INSTRUMENT_DECLINED"]}

        mock_capture.side_effect = fake_success
        badge, attendee = self.setup_upgrade()
        mock_capture.side_effect = fake_failed_capture

        form = dict(self.attendee_form_upgrade_checkout)
        form["orderID"] = "TEST-PAYPAL-UPGRADE-ORDER"
        cart, checkout = self.upgrade_add_and_checkout(self.price_90, form, badge, attendee)
        self.assertEqual(checkout.status_code, 400, checkout.content)

        badge.refresh_from_db()
        self.assertEqual(badge.effectiveLevel(), self.price_45)
        badge.delete()
        attendee.delete()

    def test_upgrade_sad_path(self):
        pass

    def test_new_staff(self):
        pass

    def test_promote_staff(self):
        pass
