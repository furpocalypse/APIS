"""
Management command to expire stale PENDING orders (abandoned checkouts).
Run periodically via cron, e.g., every 5 minutes.
"""

from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from registration.models import Order, OrderItem, PriceLevel

LEASE_TIMEOUT_MINUTES = 15


class Command(BaseCommand):
    help = "Expire PENDING orders older than the lease timeout"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=LEASE_TIMEOUT_MINUTES,
            help=f"Minutes before a PENDING order is expired (default: {LEASE_TIMEOUT_MINUTES})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report stale orders without expiring them",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(minutes=timeout)

        stale_orders = Order.objects.filter(
            status=Order.PENDING,
            createdDate__lt=cutoff,
        )

        count = stale_orders.count()
        if count == 0:
            self.stdout.write("No stale PENDING orders found.")
            return

        self.stdout.write(f"Found {count} stale PENDING order(s).")

        if dry_run:
            for order in stale_orders:
                self.stdout.write(
                    f"  Would expire: {order.reference} (created {order.createdDate})"
                )
            return

        expired = 0
        for order in stale_orders:
            with transaction.atomic():
                # Re-fetch with lock to prevent race conditions
                try:
                    order = Order.objects.select_for_update().get(id=order.id)
                except Order.DoesNotExist:
                    continue
                if order.status != Order.PENDING:
                    continue  # Already resolved

                # Count items per price level
                items = OrderItem.objects.filter(order=order).values_list(
                    "priceLevel_id", flat=True
                )
                counts = Counter(pl_id for pl_id in items if pl_id is not None)

                # Release slots
                for pl_id, qty in counts.items():
                    level = PriceLevel.objects.select_for_update().get(id=pl_id)
                    level.release_slots(qty)

                # S24 (SECURITY_BACKLOG P1 #5): already the prescribed
                # Pattern A — transaction.atomic() + select_for_update()
                # re-fetch + status==PENDING recheck above, with the
                # capacity release done under the same lock. This is
                # race-safe by construction; it is intentionally NOT
                # routed through transition_order_status (that primitive
                # would re-run the capacity transition and double-release
                # the slots already released here).
                order.status = Order.FAILED
                order.notes += f"\nAuto-expired: pending order timed out after {timeout} minutes"
                order.save()
                expired += 1
                self.stdout.write(f"  Expired: {order.reference}")

        self.stdout.write(self.style.SUCCESS(f"Expired {expired} order(s)."))
