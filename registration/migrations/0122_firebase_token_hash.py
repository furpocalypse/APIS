# Decision #8 / S21 / RT-B1: terminal bearer-token at-rest hashing.
#
# The terminal model (named ``Firebase`` for historical reasons; it is a
# Postgres-backed payment Terminal, no Firebase/FCM SDK is involved — S17)
# previously stored its bearer token in PLAINTEXT. This migration moves it
# to hash-only storage in a single, non-disruptive step:
#
#   1. Add ``token_hash`` (indexed).
#   2. Backfill ``token_hash = sha256(existing plaintext token)`` for
#      EVERY row WITHOUT regenerating any token. Already-provisioned live
#      terminals keep authenticating with their *existing, unchanged*
#      token across the deploy (zero re-provision — prevents a
#      registration-desk outage mid-event; Round-4 non-disruptive
#      invariant).
#   3. Drop the plaintext ``token`` column in THIS PR. No plaintext bearer
#      token ever persists again (ASVS V2.10.4).
#
# This makes NO constant-time claim: the honest control is hashed-at-rest
# plus a generic 401 on miss (registration.models.Firebase.find_by_token),
# not a timing-safe comparison.
#
# Reverse re-creates the ``token`` column (Django re-adds it from migration
# state with its old ``uuid4`` default) but CANNOT restore the original
# plaintext values — that data is irrecoverable by construction. A reversed
# deploy therefore requires re-provisioning every terminal (documented in
# the README/runbook, S23/S34).
#
# (The Event dealerEmail/registrationEmail/staffEmail AlterField artifacts
# that `makemigrations` emits from APIS_DEFAULT_EMAIL being read at model
# definition time are intentionally absent from 0121 and here; S21/S32. A
# mandated test asserts no such Event AlterField op exists.)

import hashlib

from django.db import migrations, models


def _backfill_token_hash(apps, schema_editor):
    # Runs while the plaintext ``token`` column still exists (RemoveField
    # is sequenced AFTER this op). Hash the EXISTING token in place; never
    # regenerate — existing devices must keep working unchanged.
    Firebase = apps.get_model("registration", "Firebase")
    for row in Firebase.objects.all().iterator():
        if row.token:
            row.token_hash = hashlib.sha256(
                row.token.encode("utf-8")
            ).hexdigest()
            row.save(update_fields=["token_hash"])


def _reverse_irrecoverable(apps, schema_editor):
    # The plaintext token was destroyed by the forward column drop and is
    # unrecoverable. Reversing only re-creates an empty column (handled by
    # the RemoveField reverse); there is no data to restore here. Reversal
    # mandates re-provisioning every terminal.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0121_alter_event_dealeremail_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="firebase",
            name="token_hash",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=64
            ),
        ),
        migrations.RunPython(_backfill_token_hash, _reverse_irrecoverable),
        migrations.RemoveField(
            model_name="firebase",
            name="token",
        ),
    ]
