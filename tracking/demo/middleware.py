"""Demo-mode middleware (design D3, D4, D9). Every class is a no-op unless DEMO_MODE."""

import logging
from datetime import timedelta

from django.conf import settings
from django.http import Http404, HttpResponseForbidden
from django.utils import timezone

from ..models import DemoState
from . import is_demo
from .reset import get_demo_user, get_state, is_reset_due, perform_reset

logger = logging.getLogger(__name__)

# The only URL names that accept unsafe-method (write) requests in demo mode.
# Everything else, including views added later, is refused by default (D4).
DEMO_ALLOWED_WRITE_URL_NAMES = frozenset({
    "add_term",
    "edit_term",
    "bulk_add",
    "bulk_edit_items",
    "add_item_source",
    "edit_item_source",
    "delete_item_source",
    "add_tag",
    "edit_tag",
    "delete_tag",
    "metadata_retry",
    "metadata_select_candidate",
    "metadata_set_external_id",
    "update",
})

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Mounted, but unreachable in demo mode (no admin, no login/logout).
BLOCKED_PATH_PREFIXES = ("/admin/", "/accounts/")


class DemoResetMiddleware:
    """Reset the sandbox on the first request after an idle period, then record activity."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_demo():
            self._maybe_reset_and_mark_activity()
        return self.get_response(request)

    @staticmethod
    def _maybe_reset_and_mark_activity():
        now = timezone.now()
        state = get_state()
        if is_reset_due(state, now):
            try:
                perform_reset(now=now)
            except Exception:
                # Serve the existing (pre-reset) data rather than failing the request.
                logger.exception("Demo reset failed; serving existing data")
            state = get_state()
        interval = timedelta(seconds=settings.DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS)
        if state.last_activity_at is None or now - state.last_activity_at >= interval:
            DemoState.objects.filter(pk=state.pk).update(last_activity_at=now)


class DemoUserMiddleware:
    """Handle every request as the fixed, non-staff demo user, with no login or session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_demo():
            path = request.path_info
            if path.startswith(BLOCKED_PATH_PREFIXES) or path.rstrip("/") in ("/admin", "/accounts"):
                raise Http404("Not available in the demo")
            request.user = get_demo_user()
        return self.get_response(request)


class DemoWriteAllowlistMiddleware:
    """Refuse unsafe-method requests to any URL name not on the demo allowlist."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not is_demo() or request.method in SAFE_METHODS:
            return None
        match = request.resolver_match
        if match is not None and match.url_name in DEMO_ALLOWED_WRITE_URL_NAMES:
            return None
        return HttpResponseForbidden("This action isn't available in the demo.")
