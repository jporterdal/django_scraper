from django.core.management.base import BaseCommand, CommandError

from tracking.demo import is_demo
from tracking.demo.reset import perform_reset


class Command(BaseCommand):
    help = (
        "Reset the demo sandbox to its seed state (run on boot before the web "
        "server starts). Refuses to run unless DEMO_MODE is enabled."
    )

    def handle(self, *args, **options):
        if not is_demo():
            # Never wipe a real deployment's data.
            raise CommandError("demo_reset only runs with DEMO_MODE=True.")
        perform_reset(force=True)
        self.stdout.write(self.style.SUCCESS("Demo sandbox reset to seed state."))
