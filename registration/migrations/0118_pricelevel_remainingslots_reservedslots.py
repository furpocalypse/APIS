from django.db import migrations, models


def initialize_counters(apps, schema_editor):
    """Set initial counter values from existing OrderItem counts."""
    PriceLevel = apps.get_model("registration", "PriceLevel")
    OrderItem = apps.get_model("registration", "OrderItem")

    for level in PriceLevel.objects.all():
        if level.maxCapacity is None:
            level.remainingSlots = None
            level.reservedSlots = 0
            level.save(update_fields=["remainingSlots", "reservedSlots"])
            continue

        confirmed = OrderItem.objects.filter(
            priceLevel=level,
            order__status__in=["Completed", "Captured"],
        ).count()
        pending = OrderItem.objects.filter(
            priceLevel=level,
            order__status="Pending",
        ).count()

        level.remainingSlots = level.maxCapacity - confirmed - pending
        level.reservedSlots = pending
        level.save(update_fields=["remainingSlots", "reservedSlots"])


def reverse_counters(apps, schema_editor):
    """No-op reverse — fields will be dropped by the schema reversal."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0117_pricelevel_capacitydisplaythreshold_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pricelevel",
            name="remainingSlots",
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text="Remaining available slots. Null means unlimited. Managed automatically.",
            ),
        ),
        migrations.AddField(
            model_name="pricelevel",
            name="reservedSlots",
            field=models.IntegerField(
                default=0,
                help_text="Slots currently held by pending payment leases.",
            ),
        ),
        migrations.RunPython(initialize_counters, reverse_counters),
    ]
