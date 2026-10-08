"""Demo mode: a public, no-login, resource-light deployment of the app.

Enabled by the single ``DEMO_MODE`` setting. See the ``demo-mode``,
``demo-replay-data`` and ``demo-sandbox-reset`` capability specs, and the
``demo-mode-sandbox`` change's design.md for the decisions referenced (D1-D10)
throughout this package.
"""

from django.conf import settings


def is_demo():
    """True when demo mode is enabled (read at call time, so tests can toggle it)."""
    return bool(getattr(settings, "DEMO_MODE", False))
