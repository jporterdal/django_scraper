"""container-deployment: the app serves its own static files in every mode."""

import tempfile
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings


class StaticServingTests(SimpleTestCase):
    def test_whitenoise_right_after_security_middleware(self):
        middleware = settings.MIDDLEWARE
        security = middleware.index("django.middleware.security.SecurityMiddleware")
        self.assertEqual(middleware[security + 1], "whitenoise.middleware.WhiteNoiseMiddleware")

    def test_collected_static_served_with_debug_and_demo_off(self):
        with tempfile.TemporaryDirectory() as static_root:
            css = Path(static_root, "admin", "css", "base.css")
            css.parent.mkdir(parents=True)
            css.write_text("body {}")
            # WhiteNoise indexes STATIC_ROOT when the middleware is built, so
            # the Client (and its handler) is created inside the override.
            with override_settings(DEBUG=False, DEMO_MODE=False, STATIC_ROOT=static_root):
                response = Client().get("/static/admin/css/base.css")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), b"body {}")
