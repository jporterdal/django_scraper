from django.apps import AppConfig


class TrackingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tracking'

    def ready(self):
        from django.conf import settings

        from .demo import protection

        # Receivers are always connected and check DEMO_MODE at runtime, so
        # override_settings(DEMO_MODE=True) in tests exercises them.
        protection.connect_signals()
        if getattr(settings, "DEMO_MODE", False):
            from .demo.egress import install_egress_guard

            install_egress_guard()
