"""S21 / S17 / Decision #8 / Round-4 mandated tests.

The terminal bearer token is stored HASH-ONLY (no plaintext column).
These cover: get_provisioning emits the token only when freshly minted;
save_model mints once on create (shown once) and never pushes a token on
routine edits; provision_view rotates only on an explicit superuser POST
and never renders an empty-token QR; the 0122 migration drops the
plaintext column and carries no spurious Event AlterField; and an
already-provisioned terminal keeps authenticating with its unchanged
token across the migration (non-disruptive).
"""

import importlib
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sites.models import Site
from django.db import connection
from django.test import RequestFactory, TestCase
from django.urls import reverse

from registration import mqtt
from registration.admin import FirebaseAdmin
from registration.models import Firebase

# Migration modules are named with a leading digit (not a valid Python
# identifier) so they must be imported by string path.
_m0121 = importlib.import_module(
    "registration.migrations.0121_alter_event_dealeremail_and_more"
)
_m0122 = importlib.import_module(
    "registration.migrations.0122_firebase_token_hash"
)

# The QR is produced by qrcode's SvgPathFillImage; this exact prefix is
# unique to it and will not collide with any admin-theme inline SVG.
_QR_MARKER = b"<?xml version='1.0' encoding='UTF-8'?>\n<svg "


def _admin_request(user, method="post"):
    """A RequestFactory request wired with session + messages so admin
    hooks (save_model uses messages on create) work off the test client."""
    req = getattr(RequestFactory(), method)("/admin/")
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    req._messages = FallbackStorage(req)
    req.user = user
    return req


class TestFirebaseAdmin(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            "admin", "admin@host", "admin"
        )
        self.normal_user = User.objects.create_user(
            "john", "john@thebeatles.com", "john"
        )
        self.normal_user.staff_member = False
        self.normal_user.save()

        # An already-provisioned terminal: only the hash is stored; we
        # keep the plaintext locally to assert auth/rotation behaviour.
        self.terminal_blue = Firebase(name="Blue")
        self.blue_token = self.terminal_blue.mint_token()
        self.terminal_blue.save()
        self.admin = FirebaseAdmin(Firebase, django_admin.site)

    # --- get_provisioning: token only when freshly supplied -----------
    def test_get_provisioning_omits_token_on_routine_display(self):
        prov = FirebaseAdmin.get_provisioning(self.terminal_blue)
        self.assertNotIn("token", prov)
        current_site = Site.objects.get_current()
        token = mqtt.get_payment_token(self.terminal_blue)
        self.assertEqual(prov["endpoint"], f"https://{current_site.domain}")
        self.assertEqual(prov["terminalName"], "Blue")
        self.assertEqual(prov["mqttUsername"], token["user"])
        self.assertEqual(prov["mqttHost"], settings.MQTT_EXTERNAL_BROKER)

    def test_get_provisioning_includes_token_only_when_minted(self):
        prov = FirebaseAdmin.get_provisioning(self.terminal_blue, token="PLAIN")
        self.assertEqual(prov["token"], "PLAIN")

    # --- TC-2: create mints once; only the hash persists --------------
    def test_save_model_create_mints_token_shown_once_only_hash_persists(self):
        captured = {}
        real_mint = Firebase.mint_token

        def _spy(inner_self):
            pt = real_mint(inner_self)
            captured["plaintext"] = pt
            return pt

        obj = Firebase(name="Fresh", background_color="#0099cc")
        with patch.object(Firebase, "mint_token", _spy), patch(
            "registration.views.onsite_admin.send_mqtt_message_to_terminal"
        ) as push:
            req = _admin_request(self.admin_user)
            self.admin.save_model(req, obj, None, change=False)
            msgs = [str(m) for m in req._messages]

        plaintext = captured["plaintext"]
        # Shown exactly once, via a message containing the plaintext.
        self.assertEqual(sum(plaintext in m for m in msgs), 1)
        # The config pushed to the device carries the token this once.
        pushed_payload = push.call_args[0][2]
        self.assertEqual(pushed_payload["token"], plaintext)
        # Only the hash persists; the plaintext is unrecoverable from DB.
        obj.refresh_from_db()
        self.assertEqual(obj.token_hash, Firebase.hash_token(plaintext))
        self.assertEqual(Firebase.find_by_token(plaintext).pk, obj.pk)

    def test_save_model_routine_edit_pushes_no_token(self):
        self.terminal_blue.name = "Blue Renamed"
        with patch(
            "registration.views.onsite_admin.send_mqtt_message_to_terminal"
        ) as push:
            req = _admin_request(self.admin_user)
            self.admin.save_model(req, self.terminal_blue, None, change=True)
        pushed_payload = push.call_args[0][2]
        self.assertNotIn("token", pushed_payload)
        self.terminal_blue.refresh_from_db()
        self.assertEqual(self.terminal_blue.name, "Blue Renamed")
        # The unchanged token still authenticates after a routine edit.
        self.assertEqual(
            Firebase.find_by_token(self.blue_token).pk, self.terminal_blue.pk
        )

    # --- TC-2: explicit rotation invalidates the prior token ----------
    def test_provision_view_get_renders_no_token_qr(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(
            reverse("admin:firebase_provision", args=(self.terminal_blue.id,))
        )
        self.assertEqual(resp.status_code, 200)
        # No QR on GET (token is not stored); prior token untouched.
        self.assertNotIn(_QR_MARKER, resp.content)
        self.assertIn(b"Rotate token", resp.content)
        self.assertEqual(
            Firebase.find_by_token(self.blue_token).pk, self.terminal_blue.pk
        )

    def test_provision_view_post_rotates_and_invalidates_prior(self):
        captured = {}
        real_mint = Firebase.mint_token

        def _spy(inner_self):
            pt = real_mint(inner_self)
            captured["plaintext"] = pt
            return pt

        self.client.force_login(self.admin_user)
        with patch.object(Firebase, "mint_token", _spy), patch(
            "registration.views.onsite_admin.send_mqtt_message_to_terminal"
        ):
            resp = self.client.post(
                reverse(
                    "admin:firebase_provision", args=(self.terminal_blue.id,)
                )
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(_QR_MARKER, resp.content)  # QR shown once on rotate
        new_token = captured["plaintext"]
        # Old token no longer resolves; the new one does.
        self.assertIsNone(Firebase.find_by_token(self.blue_token))
        self.assertEqual(
            Firebase.find_by_token(new_token).pk, self.terminal_blue.pk
        )

    def test_provision_view_post_by_non_superuser_does_not_rotate(self):
        self.client.force_login(self.normal_user)
        resp = self.client.post(
            reverse("admin:firebase_provision", args=(self.terminal_blue.id,))
        )
        self.assertIn(
            b"You must be a superuser to access this URL", resp.content
        )
        # No rotation occurred: the original token still authenticates.
        self.assertEqual(
            Firebase.find_by_token(self.blue_token).pk, self.terminal_blue.pk
        )

    # --- non-disruptive backfill invariant ----------------------------
    def test_existing_token_still_authenticates_post_migration(self):
        # 0122 backfill computes token_hash = sha256(existing plaintext)
        # and never regenerates. A row whose hash was produced that way
        # from a fixed historical token must still authenticate with that
        # SAME unchanged token via find_by_token (the lookup formula
        # equals the backfill formula). Auth-layer proof of the Round-4
        # non-disruptive invariant.
        legacy_plain = "legacy-device-token-unchanged"
        row = Firebase(name="Legacy")
        row.token_hash = Firebase.hash_token(legacy_plain)  # == backfill
        row.save()
        self.assertEqual(Firebase.find_by_token(legacy_plain).pk, row.pk)

    def test_empty_or_wrong_token_returns_none(self):
        self.assertIsNone(Firebase.find_by_token(""))
        self.assertIsNone(Firebase.find_by_token(None))
        self.assertIsNone(Firebase.find_by_token("not-a-real-token"))

    # --- TC-1: schema + migration shape -------------------------------
    def test_plaintext_token_column_absent_after_migrations(self):
        with connection.cursor() as cursor:
            cols = [
                c.name
                for c in connection.introspection.get_table_description(
                    cursor, Firebase._meta.db_table
                )
            ]
        self.assertNotIn("token", cols)
        self.assertIn("token_hash", cols)

    def test_migration_0122_drops_token_and_no_event_alterfield(self):
        ops = _m0122.Migration.operations
        removed = [
            o
            for o in ops
            if o.__class__.__name__ == "RemoveField"
            and o.model_name == "firebase"
            and o.name == "token"
        ]
        self.assertEqual(len(removed), 1)
        # S21/S32: no spurious Event AlterField op in 0122 or 0121.
        for mig in (_m0122, _m0121):
            event_alters = [
                o
                for o in mig.Migration.operations
                if o.__class__.__name__ == "AlterField"
                and getattr(o, "model_name", "").lower() == "event"
            ]
            self.assertEqual(event_alters, [])

    def test_get_qrcode(self):
        qr_code = FirebaseAdmin.get_qrcode("foo")
        self.assertIn(_QR_MARKER, qr_code)
        self.assertIn(b'height="29mm"', qr_code)

    def test_provision_page_normal_user(self):
        self.client.force_login(self.normal_user)
        response = self.client.get(
            reverse("admin:firebase_provision", args=(self.terminal_blue.id,))
        )
        self.assertIn(
            b"You must be a superuser to access this URL", response.content
        )

    def test_change_form_superuser(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                "admin:registration_firebase_change",
                args=(self.terminal_blue.id,),
            )
        )
        self.assertIn(b"Provision App", response.content)

    def test_change_form_normal_user(self):
        self.client.force_login(self.normal_user)
        response = self.client.get(
            reverse(
                "admin:registration_firebase_change",
                args=(self.terminal_blue.id,),
            )
        )
        self.assertNotIn(b"Provision App", response.content)
