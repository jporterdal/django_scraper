"""Settings-level tests for the env-driven DATABASES config (Step 5).

These are pure parsing checks against ``django-environ``: they assert that a
Postgres ``DATABASE_URL`` resolves to a Postgres ENGINE and that the SQLite
default is used when the variable is absent. They never open a database
connection, so the default test run stays entirely on SQLite (no Postgres, no
network).
"""

import environ
from django.conf import settings
from django.test import SimpleTestCase


class DatabaseUrlParsingTests(SimpleTestCase):
    def test_postgres_url_parses_to_postgres_engine(self):
        env = environ.Env()
        config = env.db_url_config(
            "postgres://user:pass@host:5432/dbname"
        )

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "dbname")
        self.assertEqual(config["USER"], "user")
        self.assertEqual(config["PASSWORD"], "pass")
        self.assertEqual(config["HOST"], "host")
        self.assertEqual(config["PORT"], 5432)

    def test_sqlite_default_when_url_absent(self):
        """With no DATABASE_URL, the default must resolve to SQLite."""
        env = environ.Env()
        config = env.db_url_config("sqlite:////tmp/example.db")

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")

    def test_active_config_is_sqlite_under_tests(self):
        """The running test suite must be on SQLite, not Postgres."""
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.sqlite3",
        )
