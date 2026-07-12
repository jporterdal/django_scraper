"""Test settings: explicit, .env-independent overrides for the test suite.

Run the suite with:

    python manage.py test tracking --settings=django_scraper.settings_test

Coverage workflow:

    coverage run manage.py test tracking --settings=django_scraper.settings_test
    coverage report -m
"""

from .settings import *  # noqa: F401,F403

DEBUG = False

# Belt-and-suspenders: keep TLS/HTTPS hardening off even if .env sets
# SECURE_DEPLOYMENT=True (settings_test imports base settings first).
SECURE_DEPLOYMENT = False
SECURE_SSL_REDIRECT = False

# Force synchronous task execution regardless of DEBUG/.env so background
# flows run inline with no Redis.
HUEY["immediate"] = True  # noqa: F405

# Fast, insecure hasher — tests do not need real password strength.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Explicit in-memory SQLite (Django already defaults the SQLite *test* DB to
# :memory:, but state it so the intent is unambiguous and .env-independent).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
