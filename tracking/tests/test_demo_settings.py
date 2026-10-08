"""demo-mode: one variable enables demo mode and forces its settings (tasks 1.1-1.3)."""

import tempfile

from django.conf import settings
from django.test import SimpleTestCase

from .demo_base import DEMO_REQUEST_SETTINGS, DemoTestCase, run_demo_subprocess

SETTINGS_SCRIPT = """
from django.conf import settings
print(json.dumps({
    "DEMO_MODE": settings.DEMO_MODE,
    "DEBUG": settings.DEBUG,
    "REDIS_URL": settings.REDIS_URL,
    "HUEY_IMMEDIATE": settings.HUEY["immediate"],
    "DB_ENGINE": settings.DATABASES["default"]["ENGINE"],
    "DB_NAME": str(settings.DATABASES["default"]["NAME"]),
    "DB_OPTIONS": settings.DATABASES["default"].get("OPTIONS", {}),
    "SCRAPE_REQUEST_DELAY_SECONDS": settings.SCRAPE_REQUEST_DELAY_SECONDS,
    "SCRAPE_REQUEST_DELAY_JITTER_SECONDS": settings.SCRAPE_REQUEST_DELAY_JITTER_SECONDS,
    "SESSION_ENGINE": settings.SESSION_ENGINE,
    "MESSAGE_STORAGE": settings.MESSAGE_STORAGE,
    "DATA_UPLOAD_MAX_MEMORY_SIZE": settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
    "DATA_UPLOAD_MAX_NUMBER_FIELDS": settings.DATA_UPLOAD_MAX_NUMBER_FIELDS,
    "MIDDLEWARE": settings.MIDDLEWARE,
    "LOG_HANDLERS": settings.LOGGING["loggers"]["tracking"]["handlers"],
    "DEMO_RESET_IDLE_SECONDS": settings.DEMO_RESET_IDLE_SECONDS,
    "DEMO_RESET_MIN_INTERVAL_SECONDS": settings.DEMO_RESET_MIN_INTERVAL_SECONDS,
}))
"""


class DemoSettingsDefaultsTests(SimpleTestCase):
    def test_demo_mode_off_by_default_with_inert_demo_settings(self):
        self.assertFalse(settings.DEMO_MODE)
        self.assertEqual(settings.DEMO_RESET_IDLE_SECONDS, 3600)
        self.assertEqual(settings.DEMO_RESET_MIN_INTERVAL_SECONDS, 3600)

    def test_demo_middleware_always_installed(self):
        for name in (
            "tracking.demo.middleware.DemoResetMiddleware",
            "tracking.demo.middleware.DemoUserMiddleware",
            "tracking.demo.middleware.DemoWriteAllowlistMiddleware",
        ):
            self.assertIn(name, settings.MIDDLEWARE)


class DemoSettingsBlockTests(SimpleTestCase):
    """The real settings module under DEMO_MODE=True, in a fresh interpreter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.values = run_demo_subprocess(
            SETTINGS_SCRIPT,
            cls._tmp.name,
            extra_env={
                "DEBUG": "True",
                "DATABASE_URL": "postgres://nobody:nothing@nowhere.invalid:5432/none",
                "REDIS_URL": "redis://nowhere.invalid:6379/0",
                "SCRAPE_REQUEST_DELAY_SECONDS": "9",
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        super().tearDownClass()

    def test_forces_debug_off(self):
        self.assertTrue(self.values["DEMO_MODE"])
        self.assertFalse(self.values["DEBUG"])
        self.assertEqual(self.values["LOG_HANDLERS"], ["console"])

    def test_no_redis_and_inline_tasks(self):
        self.assertEqual(self.values["REDIS_URL"], "")
        self.assertTrue(self.values["HUEY_IMMEDIATE"])

    def test_local_sqlite_ignoring_database_url(self):
        self.assertEqual(self.values["DB_ENGINE"], "django.db.backends.sqlite3")
        self.assertTrue(self.values["DB_NAME"].endswith("demo.sqlite3"))
        self.assertEqual(self.values["DB_OPTIONS"]["init_command"], "PRAGMA journal_mode=WAL;")
        self.assertEqual(self.values["DB_OPTIONS"]["transaction_mode"], "IMMEDIATE")
        self.assertGreater(self.values["DB_OPTIONS"]["timeout"], 0)

    def test_zero_scrape_delay(self):
        self.assertEqual(self.values["SCRAPE_REQUEST_DELAY_SECONDS"], 0)
        self.assertEqual(self.values["SCRAPE_REQUEST_DELAY_JITTER_SECONDS"], 0)

    def test_request_settings_match_test_mirror(self):
        for key, expected in DEMO_REQUEST_SETTINGS.items():
            self.assertEqual(self.values[key], expected, key)

    def test_whitenoise_after_security_middleware(self):
        middleware = self.values["MIDDLEWARE"]
        security = middleware.index("django.middleware.security.SecurityMiddleware")
        self.assertEqual(middleware[security + 1], "whitenoise.middleware.WhiteNoiseMiddleware")


class DemoResetSettingsOverrideTests(SimpleTestCase):
    def test_reset_settings_overridable_by_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = run_demo_subprocess(
                SETTINGS_SCRIPT,
                tmp,
                extra_env={
                    "DEMO_RESET_IDLE_SECONDS": "600",
                    "DEMO_RESET_MIN_INTERVAL_SECONDS": "1800",
                },
            )
        self.assertEqual(values["DEMO_RESET_IDLE_SECONDS"], 600)
        self.assertEqual(values["DEMO_RESET_MIN_INTERVAL_SECONDS"], 1800)


class DemoBlockedPathsTests(DemoTestCase):
    def test_admin_and_accounts_return_404(self):
        for path in ("/admin/", "/admin/login/", "/accounts/login/", "/accounts/logout/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
