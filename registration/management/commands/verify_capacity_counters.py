"""
Management command to verify capacity counters against ground truth.
Can be run periodically or manually to detect and optionally repair drift.
"""

from django.core.management.base import BaseCommand

from registration.models import PriceLevel


class Command(BaseCommand):
    help = "Verify capacity counters against ground-truth OrderItem counts"

    def add_arguments(self, parser):
        parser.add_argument(
            "--repair",
            action="store_true",
            help="Repair any detected drift (default: report only)",
        )

    def handle(self, *args, **options):
        repair = options["repair"]
        levels = PriceLevel.objects.filter(maxCapacity__isnull=False)

        if not levels.exists():
            self.stdout.write("No price levels with capacity limits found.")
            return

        drift_count = 0
        for level in levels:
            actual_confirmed = level.verify_registration_count()
            actual_pending = level.verify_pending_count()
            expected_remaining = level.maxCapacity - actual_confirmed - actual_pending

            ok = (
                level.remainingSlots == expected_remaining
                and level.reservedSlots == actual_pending
            )

            if ok:
                self.stdout.write(
                    f"  OK: {level.name} "
                    f"(remaining={level.remainingSlots}, reserved={level.reservedSlots})"
                )
            else:
                drift_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  DRIFT: {level.name} — "
                        f"remaining={level.remainingSlots} (expected {expected_remaining}), "
                        f"reserved={level.reservedSlots} (expected {actual_pending})"
                    )
                )

                if repair:
                    level.remainingSlots = expected_remaining
                    level.reservedSlots = actual_pending
                    level.save(update_fields=["remainingSlots", "reservedSlots"])
                    self.stdout.write(self.style.SUCCESS(f"    Repaired: {level.name}"))

        if drift_count == 0:
            self.stdout.write(self.style.SUCCESS("All counters are correct."))
        else:
            verb = "Repaired" if repair else "Found"
            self.stdout.write(
                self.style.WARNING(f"{verb} drift in {drift_count} price level(s).")
            )
            if not repair:
                self.stdout.write("Run with --repair to fix.")
