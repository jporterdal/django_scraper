"""Shared demo-mode test helpers (not collected as tests)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from tracking.demo import dataset
from tracking.demo.reset import perform_reset

# The request-handling settings the ``if DEMO_MODE:`` block in settings.py
# forces. test_demo_settings asserts the real block produces these values.
DEMO_REQUEST_SETTINGS = {
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
    "MESSAGE_STORAGE": "django.contrib.messages.storage.cookie.CookieStorage",
    "DATA_UPLOAD_MAX_MEMORY_SIZE": 64 * 1024,
    "DATA_UPLOAD_MAX_NUMBER_FIELDS": 1000,
}

SEED_ITEM_PKS = sorted(dataset.seed_item_pks())
SEED_TAG_PKS = sorted(dataset.seed_tag_pks())


@override_settings(DEMO_MODE=True, DEMO_SEED_HISTORY_DAYS=3, **DEMO_REQUEST_SETTINGS)
class DemoTestCase(TestCase):
    """Demo mode on, seeded once per class (short history keeps the suite fast)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        perform_reset(force=True)

    def visitor_item(self, text="Vexing Starfall", sources=("dragonhoard",)):
        from tracking.models import ItemSource, SearchableItem

        item = SearchableItem.objects.create(text=text)
        for source_key in sources:
            ItemSource.objects.create(item=item, source_id=source_key)
        return item

    def messages_text(self, response):
        return [str(m) for m in response.context["messages"]] if response.context else []


def run_demo_subprocess(script, tmp_path, extra_env=None, timeout=120):
    """Run ``script`` in a fresh interpreter with the real settings and DEMO_MODE=True.

    Used where in-process override_settings can't reach: the settings block
    itself, and file-backed SQLite (WAL/IMMEDIATE) concurrency. ``script`` is
    run after ``django.setup()`` and must print one JSON document on its last
    line, which is returned decoded.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DEMO_", "DJANGO_SETTINGS_MODULE"))
    }
    env.update({
        "DJANGO_SETTINGS_MODULE": "django_scraper.settings",
        "DEMO_MODE": "True",
        "SECRET_KEY": "demo-subprocess-test",
        "ALLOWED_HOSTS": "localhost,testserver",
        "DEMO_DATABASE_PATH": str(Path(tmp_path) / "demo.sqlite3"),
    })
    env.update(extra_env or {})
    prelude = "import django, json\ndjango.setup()\n"
    result = subprocess.run(
        [sys.executable, "-c", prelude + script],
        cwd=settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(f"demo subprocess failed:\n{result.stderr[-4000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])
