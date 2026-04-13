from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0117_alter_paymentwebhooknotification_event_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="email_sent",
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="email_error",
            field=models.TextField(blank=True, default=""),
        ),
    ]
