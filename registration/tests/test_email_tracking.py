"""Tests for email-delivery tracking on Order (email_sent, email_error).

Covers the Celery task paths in registration.tasks that record success or
final-retry-exhausted failure onto the Order the confirmation email was for.
Runs with ``CELERY_TASK_ALWAYS_EAGER`` so ``.delay()`` executes inline.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings, tag

from registration import tasks
from registration.models import Order


@tag("PayPal")
@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class TestRegistrationEmailTracking(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            total="10.00",
            reference="EMAILTRK",
            billingEmail="user@example.test",
        )

    @patch("registration.emails.send_registration_email")
    def test_success_sets_email_sent_true(self, mock_send):
        mock_send.return_value = None
        tasks.send_registration_email_task.delay(self.order.id, self.order.billingEmail)
        self.order.refresh_from_db()
        self.assertIs(self.order.email_sent, True)
        self.assertEqual(self.order.email_error, "")

    @patch("registration.emails.send_registration_email")
    def test_final_failure_sets_email_sent_false(self, mock_send):
        """Drive the task through max_retries+1 calls so the last attempt
        hits the retries-exhausted branch and marks the order as failed."""
        from celery.exceptions import MaxRetriesExceededError

        mock_send.side_effect = RuntimeError("smtp down")
        task = tasks.send_registration_email_task
        with self.assertRaises((MaxRetriesExceededError, RuntimeError)):
            task.apply(
                args=[self.order.id, self.order.billingEmail],
                throw=True,
                retries=task.max_retries,
            )
        self.order.refresh_from_db()
        self.assertIs(self.order.email_sent, False)
        self.assertIn("smtp down", self.order.email_error)

    @patch("registration.emails.send_dealer_payment_email")
    def test_dealer_payment_success(self, mock_send):
        from registration.models import Attendee, Dealer, Event
        from registration.tests.common import (
            DEFAULT_EVENT_ARGS,
            TEST_ATTENDEE_ARGS,
        )

        event = Event.objects.create(**DEFAULT_EVENT_ARGS)
        attendee = Attendee.objects.create(**TEST_ATTENDEE_ARGS)
        dealer = Dealer.objects.create(attendee=attendee, event=event)
        mock_send.return_value = None

        tasks.send_dealer_payment_email_task.delay(dealer.id, self.order.id)
        self.order.refresh_from_db()
        self.assertIs(self.order.email_sent, True)

    @patch("registration.emails.send_upgrade_payment_email")
    def test_upgrade_payment_success(self, mock_send):
        from registration.models import Attendee
        from registration.tests.common import TEST_ATTENDEE_ARGS

        attendee = Attendee.objects.create(**TEST_ATTENDEE_ARGS)
        mock_send.return_value = None

        tasks.send_upgrade_payment_email_task.delay(attendee.id, self.order.id)
        self.order.refresh_from_db()
        self.assertIs(self.order.email_sent, True)

    @patch("registration.emails.send_dealer_assistant_email")
    def test_dealer_assistant_success_updates_order_when_order_id_given(
        self, mock_send
    ):
        from registration.models import Attendee, Dealer, Event
        from registration.tests.common import (
            DEFAULT_EVENT_ARGS,
            TEST_ATTENDEE_ARGS,
        )

        event = Event.objects.create(**DEFAULT_EVENT_ARGS)
        attendee = Attendee.objects.create(**TEST_ATTENDEE_ARGS)
        dealer = Dealer.objects.create(attendee=attendee, event=event)
        mock_send.return_value = None

        tasks.send_dealer_assistant_email_task.delay(dealer.id, self.order.id)
        self.order.refresh_from_db()
        self.assertIs(self.order.email_sent, True)

    @patch("registration.emails.send_dealer_assistant_email")
    def test_dealer_assistant_success_no_order_id_still_works(self, mock_send):
        from registration.models import Attendee, Dealer, Event
        from registration.tests.common import (
            DEFAULT_EVENT_ARGS,
            TEST_ATTENDEE_ARGS,
        )

        event = Event.objects.create(**DEFAULT_EVENT_ARGS)
        attendee = Attendee.objects.create(**TEST_ATTENDEE_ARGS)
        dealer = Dealer.objects.create(attendee=attendee, event=event)
        mock_send.return_value = None

        tasks.send_dealer_assistant_email_task.delay(dealer.id)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.email_sent)
