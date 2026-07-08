"""Background tasks (Huey).

In local/dev and under the test suite Huey runs in **immediate mode** (see
``HUEY["immediate"]`` in settings, defaulting to ``DEBUG``), so enqueuing a task
runs it inline in-process with no Redis needed. In production, run the consumer
with ``python manage.py run_huey`` against a live Redis.
"""

import logging

from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import periodic_task, task

from .models import ItemSource, SearchableItem, UpdateSchedule, WebUpdate

logger = logging.getLogger(__name__)


@task()
def run_web_update_task(webupdate_id, item_ids=None):
    """Run a web update for a pre-created ``WebUpdate`` in the background.

    Loads the ``WebUpdate`` row, resolves the active items from ``item_ids``
    (``None`` = all active items), and delegates to ``run_web_update``. On any
    unhandled error the ``WebUpdate`` is marked ``FAILED`` so the progress UI
    can stop polling.
    """
    # Imported lazily so importing this module never drags in the orchestrator.
    from .scrape import run_web_update

    try:
        webupdate = WebUpdate.objects.get(pk=webupdate_id)
    except WebUpdate.DoesNotExist:
        logger.error("run_web_update_task: WebUpdate %s does not exist", webupdate_id)
        return None

    items = None
    if item_ids:
        items = SearchableItem.objects.filter(pk__in=item_ids, active=True)

    try:
        return run_web_update(items=items, webupdate=webupdate)
    except Exception:
        logger.exception(
            "run_web_update_task failed for WebUpdate %s", webupdate_id
        )
        webupdate.status = WebUpdate.Status.FAILED
        webupdate.save(update_fields=["status"])
        raise


# ---------------------------------------------------------------------------
# Phase 3 Step 4 — recurring schedules.
#
# The helpers below (``get_due_schedules`` / ``resolve_schedule_item_ids`` /
# ``dispatch_due_schedules``) hold all the due-resolution and tag/item-scope
# logic so they can be unit-tested without a running Huey consumer. The
# ``@periodic_task`` at the bottom is the only worker-only piece: it wakes every
# minute and delegates to ``dispatch_due_schedules``.
# ---------------------------------------------------------------------------


def get_due_schedules(now=None):
    """Return the enabled ``UpdateSchedule`` rows that are due at ``now``.

    ``now`` defaults to the current time (aware, UTC). Due-checking is delegated
    to ``UpdateSchedule.is_due`` so the cadence logic lives on the model.
    """
    if now is None:
        now = timezone.now()
    return [
        schedule
        for schedule in UpdateSchedule.objects.filter(enabled=True)
        if schedule.is_due(now)
    ]


def resolve_schedule_item_ids(schedule):
    """Resolve the active item ids a schedule should scrape.

    * No tag → ``None`` (``run_web_update_task`` interprets this as "all active
      items", matching the manual "Update All Active" flow).
    * A tag → the pks of active items carrying that tag (may be an empty list
      when the tag matches no active items).
    """
    if schedule.tag_id is None:
        return None
    return list(
        SearchableItem.objects.filter(active=True, tags=schedule.tag_id)
        .values_list("pk", flat=True)
    )


def _planned_search_count(item_ids):
    """Number of item-source searches a run for ``item_ids`` will attempt."""
    qs = ItemSource.objects.filter(item__active=True)
    if item_ids is not None:
        qs = qs.filter(item__in=item_ids)
    return qs.count()


def dispatch_due_schedules(now=None):
    """Enqueue a background run for every due schedule and stamp ``last_run_at``.

    Returns a list of ``(schedule, webupdate)`` tuples for the runs enqueued.
    Kept free of any worker-only imports so it is fully testable with immediate
    Huey (no Redis, no worker).
    """
    if now is None:
        now = timezone.now()

    dispatched = []
    for schedule in get_due_schedules(now):
        item_ids = resolve_schedule_item_ids(schedule)

        # A tag-scoped schedule whose tag has no active items has nothing to
        # scrape. Stamp it anyway so we don't re-check it every minute, and
        # never fall through to the "all active" path (item_ids == [] would be
        # treated as "all" by run_web_update_task).
        if schedule.tag_id is not None and not item_ids:
            logger.info(
                "Schedule %s (%s) is due but its tag has no active items; skipping",
                schedule.pk,
                schedule.name,
            )
            schedule.last_run_at = now
            schedule.save(update_fields=["last_run_at"])
            continue

        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.PENDING,
            total_searches=_planned_search_count(item_ids),
        )
        run_web_update_task(webupdate.pk, item_ids=item_ids)

        schedule.last_run_at = now
        schedule.save(update_fields=["last_run_at"])
        dispatched.append((schedule, webupdate))

    return dispatched


@periodic_task(crontab(minute="*"))
def dispatch_scheduled_updates():
    """Huey periodic task: every minute, enqueue runs for any due schedules.

    The crontab fires once a minute; the real cadence lives in the DB
    (``UpdateSchedule`` interval + ``anchor_time``). This only runs inside the
    Huey consumer (``python manage.py run_huey``) — immediate mode has no
    scheduler, so real periodic scheduling is a long-running-process feature.
    """
    dispatched = dispatch_due_schedules()
    if dispatched:
        logger.info("Dispatched %d scheduled update(s)", len(dispatched))
    return len(dispatched)
