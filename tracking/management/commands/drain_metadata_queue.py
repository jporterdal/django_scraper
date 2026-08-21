from django.core.management.base import BaseCommand

from tracking.tasks import drain_pending_metadata_fetch_requests


class Command(BaseCommand):
    """Dev-mode escape hatch for the metadata fetch queue (design.md decision 10).

    Wraps ``drain_pending_metadata_fetch_requests`` so it can be run on demand
    without a live ``run_huey`` consumer — useful under ``HUEY_IMMEDIATE=True``
    (the dev/test default), where ``run_huey`` cannot start at all.
    """

    help = "Drain pending MetadataFetchRequest rows without a running Huey consumer."

    def handle(self, *args, **options):
        processed = drain_pending_metadata_fetch_requests()
        if processed:
            self.stdout.write(self.style.SUCCESS(f"Drained {processed} metadata fetch request(s)."))
        else:
            self.stdout.write("No pending metadata fetch requests.")
