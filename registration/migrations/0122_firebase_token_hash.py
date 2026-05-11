# Audit P1.9: hash Firebase.token at rest.
#
# Adds the token_hash column and backfills it for existing rows so that
# already-provisioned terminals continue to authenticate via
# `Firebase.find_by_token` (constant-time SHA-256 compare). The plaintext
# `token` column is kept transitionally — a future cleanup will null it
# out once admin-side token rotation has been performed.
#
# Event-email AlterField operations from `manage.py makemigrations` were
# stripped; they were artifacts of APIS_DEFAULT_EMAIL being read at model
# definition time.

import hashlib

from django.db import migrations, models


def _backfill_token_hash(apps, schema_editor):
    Firebase = apps.get_model("registration", "Firebase")
    for row in Firebase.objects.all().iterator():
        if row.token:
            row.token_hash = hashlib.sha256(row.token.encode("utf-8")).hexdigest()
            row.save(update_fields=["token_hash"])


def _noop_reverse(apps, schema_editor):
    # Reversing this migration just drops the column; no data restoration
    # needed (the plaintext token is still present).
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
        migrations.RunPython(_backfill_token_hash, _noop_reverse),
    ]
