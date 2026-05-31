# S33 HIGH-2 (OWASP API1 BOLA): bind an Order to the terminal that opened
# it so a second terminal cannot complete another terminal's order.
#
# Fail-safe by construction: the column is nullable with no backfill. Every
# pre-existing order — and any order opened before the binding code path
# runs — has ``opened_at_terminal IS NULL``, and the completion guards in
# registration.views.onsite_admin are inert when it is NULL. Enabling this
# therefore cannot break an in-flight or historical checkout. ``SET_NULL``
# on terminal deletion keeps the order completable rather than orphaning it.
#
# Single nullable AddField, no data migration (no plaintext/PII touched).

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0122_firebase_token_hash"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="opened_at_terminal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="opened_orders",
                to="registration.firebase",
            ),
        ),
    ]
