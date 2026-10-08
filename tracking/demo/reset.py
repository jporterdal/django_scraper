"""Demo sandbox reset: restore the seed state atomically (design D9).

A reset deletes every ``tracking`` table except ``DemoState``, reloads the
seed manifest with its fixed PKs, and regenerates seed price history by
running the real scrape pipeline against the replay fetcher at simulated
past timestamps, so FetchJobs, dedup counts and observed category values are
all produced by the same code a live update uses.

``flush`` is deliberately not used: it would also wipe auth tables and reset
SQLite's AUTOINCREMENT sequences, letting a new visitor item reuse the PK of
a removed one.
"""

import logging
from contextlib import contextmanager
from datetime import time as dt_time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from ..models import (
    DemoState,
    FetchJob,
    ItemMetadata,
    ItemSource,
    MetadataFetchRequest,
    ObservedCategoryValue,
    SearchableItem,
    SearchResult,
    Source,
    Tag,
    UpdateSchedule,
    WebUpdate,
)
from . import dataset
from .protection import allow_seed_writes

logger = logging.getLogger(__name__)

DEMO_METADATA_PROVIDER_KEY = "demo"


def get_state():
    state, _ = DemoState.objects.get_or_create(pk=1)
    return state


def is_reset_due(state, now):
    """Idle for at least DEMO_RESET_IDLE_SECONDS *and* DEMO_RESET_MIN_INTERVAL_SECONDS since the last reset.

    Activity is only written once per DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS,
    so the stored value can trail the true last request by up to that much;
    adding the interval keeps "idle" a true lower bound, so an active visitor
    is never reset underneath.
    """
    if state.last_reset_at is None:
        return True
    if state.last_activity_at is not None:
        idle_needed = timedelta(
            seconds=settings.DEMO_RESET_IDLE_SECONDS
            + settings.DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS
        )
        if now - state.last_activity_at < idle_needed:
            return False
    return now - state.last_reset_at >= timedelta(
        seconds=settings.DEMO_RESET_MIN_INTERVAL_SECONDS
    )


def perform_reset(force=False, now=None):
    """Reset to the seed state if due (or unconditionally with ``force``).

    Runs in one transaction; in demo mode that is ``BEGIN IMMEDIATE`` (D2),
    so concurrent callers serialize on the write lock and every caller after
    the first re-checks the state, finds it no longer due, and returns.
    Returns True when this call performed the reset.
    """
    now = now or timezone.now()
    with transaction.atomic():
        state = get_state()
        if not force and not is_reset_due(state, now):
            return False
        with allow_seed_writes():
            _wipe()
            _ensure_demo_user()
            _load_seed(now)
            _generate_history(now)
        state.last_reset_at = now
        state.save(update_fields=["last_reset_at"])
    logger.info("Demo sandbox reset to seed state")
    return True


def _wipe():
    # Children first, so cascades have nothing left to collect row by row.
    for model in (
        SearchResult,
        FetchJob,
        WebUpdate,
        MetadataFetchRequest,
        ItemMetadata,
        ItemSource,
        SearchableItem.tags.through,
        SearchableItem,
        UpdateSchedule,
        Tag,
        ObservedCategoryValue,
        Source,
    ):
        model.objects.all().delete()


def _ensure_demo_user():
    username = dataset.seed()["user"]["username"]
    user, created = get_user_model().objects.get_or_create(username=username)
    if created or user.is_staff or user.is_superuser or user.has_usable_password():
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()
    return user


def get_demo_user():
    username = dataset.seed()["user"]["username"]
    try:
        return get_user_model().objects.get(username=username)
    except get_user_model().DoesNotExist:
        return _ensure_demo_user()


def _load_seed(now):
    for source in dataset.sources():
        Source.objects.create(
            key=source["key"],
            name=source["name"],
            parser_key=source["parser_key"],
            base_search_url=dataset.demo_search_url_template(source["key"]),
            http_method=Source.HttpMethod.GET,
            max_pages=1,
            rate_limit_profile="",
        )

    seed = dataset.seed()
    tags = {}
    for tag in seed["tags"]:
        tags[tag["pk"]] = Tag.objects.create(pk=tag["pk"], name=tag["name"], color=tag["color"])

    entries = {entry["id"]: entry for entry in dataset.metadata_entries()}
    for spec in seed["items"]:
        external_id = spec["metadata_external_id"]
        item = SearchableItem.objects.create(
            pk=spec["pk"],
            text=spec["text"],
            priority=spec["priority"],
            active=True,
            metadata_provider_key=DEMO_METADATA_PROVIDER_KEY if external_id else "",
            expected_product_line=spec["expected_product_line"],
            expected_category=spec["expected_category"],
        )
        item.tags.set([tags[pk] for pk in spec["tags"]])
        for item_source in spec["sources"]:
            ItemSource.objects.create(
                item=item,
                source_id=item_source["source"],
                title_include_patterns=item_source["include"],
                title_exclude_patterns=item_source["exclude"],
            )
        if external_id:
            ItemMetadata.objects.create(
                item=item,
                status=ItemMetadata.Status.MATCHED,
                external_id=external_id,
                pinned_external_id=external_id,
                payload=dict(entries[external_id]),
                fetched_at=now,
            )

    for schedule in seed["schedules"]:
        UpdateSchedule.objects.create(
            name=schedule["name"],
            frequency=schedule["frequency"],
            anchor_time=dt_time.fromisoformat(schedule["anchor_time"]),
            tag=tags.get(schedule["tag"]),
            enabled=schedule["enabled"],
        )


@contextmanager
def _quiet(logger_name):
    """Silence per-unit INFO logging while generating many simulated runs."""
    target = logging.getLogger(logger_name)
    previous = target.level
    target.setLevel(logging.WARNING)
    try:
        yield
    finally:
        target.setLevel(previous)


# The newest simulated run is stamped slightly before the reset, so a visitor's
# first update right after a reset falls in a different drift-noise minute and
# records new prices instead of all-"unchanged".
HISTORY_END_OFFSET = timedelta(minutes=5)


def _generate_history(now):
    from ..scrape import run_web_update
    from .replay import ReplayFetcher

    days = settings.DEMO_SEED_HISTORY_DAYS
    seed_items = SearchableItem.objects.filter(pk__in=dataset.seed_item_pks())
    end = now - HISTORY_END_OFFSET
    with _quiet("tracking.scrape"):
        for offset in range(days - 1, -1, -1):
            when = end - timedelta(days=offset)
            webupdate = WebUpdate.objects.create()
            run_web_update(
                items=seed_items,
                fetcher=ReplayFetcher(clock=lambda when=when: when),
                webupdate=webupdate,
            )
            # auto_now_add stamped "now"; backdate to the simulated day.
            WebUpdate.objects.filter(pk=webupdate.pk).update(timestamp=when)
