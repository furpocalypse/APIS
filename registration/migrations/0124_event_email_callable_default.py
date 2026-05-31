# S32: stabilize the migration graph against APIS_DEFAULT_EMAIL.
#
# Event.registrationEmail / staffEmail / dealerEmail previously declared
# ``default=settings.APIS_DEFAULT_EMAIL`` — resolved at model-class
# definition time. Whenever the deployed/test env's APIS_DEFAULT_EMAIL
# differed from the literal baked into migration 0121, every
# ``makemigrations --check`` emitted a spurious Event ``AlterField`` (the
# long-standing S32 noise that forced an exception on every CI run).
#
# Switching the field default to the module-level callable
# ``registration.models.default_registration_email`` makes the declared
# default a stable import path that Django serializes by reference, not by
# resolved value. After this one AlterField the model state and migration
# state agree regardless of the env var, so ``makemigrations --check`` is
# clean. No data change: existing rows keep their stored values; new Event
# rows still resolve the configured address at creation time.

import registration.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("registration", "0123_order_opened_at_terminal"),
    ]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="registrationEmail",
            field=models.CharField(
                blank=True,
                default=registration.models.default_registration_email,
                help_text="Email to display on error messages for attendee registration",
                max_length=200,
                verbose_name="Registration Email",
            ),
        ),
        migrations.AlterField(
            model_name="event",
            name="staffEmail",
            field=models.CharField(
                blank=True,
                default=registration.models.default_registration_email,
                help_text="Email to display on error messages for staff registration",
                max_length=200,
                verbose_name="Staff Email",
            ),
        ),
        migrations.AlterField(
            model_name="event",
            name="dealerEmail",
            field=models.CharField(
                blank=True,
                default=registration.models.default_registration_email,
                help_text="Email to display on error messages for dealer registration",
                max_length=200,
                verbose_name="Dealer Email",
            ),
        ),
    ]
