"""Visitor caps for demo mode (design D5). Callers check ``is_demo()`` first."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .protection import visitor_items, visitor_tags


def visitor_item_limit_reached(adding=1):
    return visitor_items().count() + adding > settings.DEMO_MAX_VISITOR_ITEMS


def visitor_tag_limit_reached(adding=1):
    return visitor_tags().count() + adding > settings.DEMO_MAX_VISITOR_TAGS


def search_results_full():
    from ..models import SearchResult

    return SearchResult.objects.count() >= settings.DEMO_MAX_SEARCH_RESULTS


def claim_update_slot(now=None):
    """Atomically claim the demo-wide update slot.

    Returns ``(True, 0)`` when this caller may start a run, else ``(False,
    seconds_until_available)``. A conditional UPDATE, so two concurrent
    requests can't both win.
    """
    from ..models import DemoState
    from .reset import get_state

    now = now or timezone.now()
    interval = timedelta(seconds=settings.DEMO_UPDATE_MIN_INTERVAL_SECONDS)
    state = get_state()
    claimed = (
        DemoState.objects.filter(pk=state.pk)
        .filter(Q(last_update_started_at__isnull=True) | Q(last_update_started_at__lte=now - interval))
        .update(last_update_started_at=now)
    )
    if claimed:
        return True, 0
    state.refresh_from_db(fields=["last_update_started_at"])
    remaining = (state.last_update_started_at + interval - now).total_seconds()
    return False, max(1, int(remaining + 0.999))
