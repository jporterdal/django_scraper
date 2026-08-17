"""Background tasks (Huey).

In local/dev and under the test suite Huey runs in **immediate mode** (see
``HUEY["immediate"]`` in settings, defaulting to ``DEBUG``), so enqueuing a task
runs it inline in-process with no Redis needed. In production, run the consumer
with ``python manage.py run_huey`` against a live Redis.

Fan-out (D8): a scheduled or manual web update creates one ``WebUpdate`` and
enqueues one ``fetch_one`` task per ``ItemSource`` (see ``dispatch_fan_out``),
instead of a single task looping every item-source inline. This lets deferring
one rate-limited scope free the worker for other, ready item-sources.
"""

import logging
import time

from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import periodic_task, task

from .locks import get_unit_lock, lock_ttl_for_eta, unit_lock_key
from .models import FetchJob, ItemSource, SearchableItem, UpdateSchedule, WebUpdate
from .ratelimit import RateLimitPolicy

logger = logging.getLogger(__name__)


def _item_source_queryset(item_ids=None):
    qs = ItemSource.objects.filter(item__active=True)
    if item_ids is not None:
        qs = qs.filter(item__in=item_ids)
    return qs


def dispatch_fan_out(item_ids=None):
    """Create a ``WebUpdate`` and enqueue one ``fetch_one`` task per ItemSource (D8).

    ``item_ids`` mirrors the old ``run_web_update_task`` contract: ``None``
    means all active items; an iterable (possibly empty) scopes the run. Used
    by both the manual "Update" view and ``dispatch_due_schedules`` so there is
    a single fan-out entry point (task 4.1/4.3).

    Each unit's enqueue is guarded by the Redis ``SET NX`` lock (D11/task 5.5)
    keyed ``fetchone:{webupdate_id}:{item_source_id}``; a fresh WebUpdate's
    first fan-out always wins every lock, so this only matters for
    duplicate/racing dispatch calls.
    """
    item_source_ids = list(_item_source_queryset(item_ids).values_list("pk", flat=True))

    webupdate = WebUpdate.objects.create(
        status=WebUpdate.Status.PENDING,
        total_searches=len(item_source_ids),
    )

    if not item_source_ids:
        # No units to fan out to: terminalize() never runs, so nothing would
        # otherwise flip PENDING -> DONE and the progress UI would poll
        # forever. The manual "Update" view separately guards against this
        # by checking item_count == 0 before calling in, but a schedule can
        # reach here directly (e.g. a tag whose active items have no
        # ItemSource configured yet), so guard it here too.
        webupdate.status = WebUpdate.Status.DONE
        webupdate.save(update_fields=["status"])
        logger.info(
            "Dispatched fan-out for WebUpdate %s: 0 item-sources planned, marked DONE",
            webupdate.pk,
        )
        return webupdate

    try:
        lock = get_unit_lock()
        scheduled = 0
        for item_source_id in item_source_ids:
            lock_key = unit_lock_key(webupdate.pk, item_source_id)
            if not lock.acquire(lock_key, lock_ttl_for_eta(None)):
                logger.info(
                    "dispatch_fan_out: skipping duplicate enqueue webupdate=%s item_source=%s",
                    webupdate.pk,
                    item_source_id,
                )
                continue
            fetch_one(webupdate.pk, item_source_id, attempt=0)
            scheduled += 1
    except Exception:
        # Fan-out/orchestrator failure (D9/task 5.7): WebUpdate.FAILED is
        # reserved for exactly this — the enqueue loop itself blowing up —
        # not for a single unit's error, which terminalizes on its own
        # (fetch_one's except-and-terminalize) without ever raising back here
        # under Huey (immediate or worker mode both swallow task exceptions).
        logger.exception("dispatch_fan_out failed for WebUpdate %s", webupdate.pk)
        WebUpdate.objects.filter(pk=webupdate.pk).update(status=WebUpdate.Status.FAILED)
        raise

    logger.info(
        "Dispatched fan-out for WebUpdate %s: %d/%d unit task(s) enqueued",
        webupdate.pk,
        scheduled,
        len(item_source_ids),
    )
    return webupdate


@task()
def fetch_one(webupdate_id, item_source_id, attempt=0):
    """Per-ItemSource Huey unit of work (D8/task 4.2).

    Loads the pre-created ``WebUpdate`` and ``ItemSource``, then either
    terminalizes (success, error, empty, blocked, or give-up) or requeues
    itself for a later attempt when the rate-limit pacer defers (D3/D11).

    Idempotent: a duplicate delivery for a pair that already has a terminal
    ``FetchJob`` is a no-op (D11/task 5.6). Unexpected errors from
    ``fetch_one_unit`` always terminalize as a failed job so the WebUpdate
    barrier can still close (D9) even on a bug; the exception is re-raised
    afterwards so Huey/logging still see it.
    """
    from .scrape import _should_give_up, fetch_one_unit, terminalize

    lock = get_unit_lock()
    lock_key = unit_lock_key(webupdate_id, item_source_id)

    try:
        webupdate = WebUpdate.objects.get(pk=webupdate_id)
    except WebUpdate.DoesNotExist:
        logger.error("fetch_one: WebUpdate %s does not exist", webupdate_id)
        lock.release(lock_key)
        return None

    try:
        item_source = ItemSource.objects.select_related("item", "source").get(
            pk=item_source_id
        )
    except ItemSource.DoesNotExist:
        logger.error("fetch_one: ItemSource %s does not exist", item_source_id)
        lock.release(lock_key)
        return None

    item, source = item_source.item, item_source.source

    # Idempotent unit start (D11/5.6): a pair that already terminalized is a
    # no-op even if this delivery raced past the enqueue-time lock check
    # (e.g. the lock already expired, or Huey redelivered).
    if FetchJob.objects.filter(webupdate=webupdate, item=item, source=source).exists():
        logger.info(
            "fetch_one no-op (already terminal): webupdate=%s item_source=%s",
            webupdate_id,
            item_source_id,
        )
        lock.release(lock_key)
        return None

    try:
        try:
            result = fetch_one_unit(
                webupdate, item_source, attempt=attempt, run_id=str(webupdate_id)
            )
        except Exception:
            logger.exception(
                "fetch_one unexpected error webupdate=%s item_source=%s",
                webupdate_id,
                item_source_id,
            )
            terminalize(
                webupdate,
                item,
                source,
                item.text,
                "",
                FetchJob.Status.HTTP_ERROR,
                error_message="Unexpected error in fetch_one; see server logs",
            )
            raise

        if not result.deferred:
            return None

        decision = result.decision
        policy = RateLimitPolicy.from_settings()
        now = time.time()

        # D10: give up before requeueing when attempts or wall clock are
        # exhausted, or when the pacer itself signaled "no useful eta"
        # (per-run cap trip — see pacer.PaceDecision docs).
        if decision.eta is None or _should_give_up(webupdate, attempt, policy, now):
            terminalize(
                webupdate,
                item,
                source,
                item.text,
                "",
                FetchJob.Status.GIVE_UP,
                error_message=(
                    f"Gave up after {attempt} defer attempt(s) (reason={decision.reason})"
                ),
            )
            logger.info(
                "ratelimit give_up webupdate=%s item_source=%s attempt=%s reason=%s",
                webupdate_id,
                item_source_id,
                attempt,
                decision.reason,
            )
            return None

        delay = max(0.0, decision.eta - now)
        lock.release(lock_key)
        if lock.acquire(lock_key, lock_ttl_for_eta(decision.eta, now)):
            fetch_one.schedule(
                kwargs={
                    "webupdate_id": webupdate_id,
                    "item_source_id": item_source_id,
                    "attempt": attempt + 1,
                },
                delay=delay,
            )
            logger.info(
                "ratelimit defer requeue webupdate=%s item_source=%s next_attempt=%s "
                "delay=%.1fs reason=%s",
                webupdate_id,
                item_source_id,
                attempt + 1,
                delay,
                decision.reason,
            )
        else:
            logger.warning(
                "fetch_one requeue skipped (lock held): webupdate=%s item_source=%s",
                webupdate_id,
                item_source_id,
            )
        return None
    finally:
        # A terminal outcome (success/error/give-up) always releases the
        # lock; the requeue branch above already replaced it with a fresh
        # TTL, so release-again here is a safe no-op for that path.
        lock.release(lock_key)


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

    * No tag → ``None`` (``dispatch_fan_out`` interprets this as "all active
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
        # treated as "all" by dispatch_fan_out).
        if schedule.tag_id is not None and not item_ids:
            logger.info(
                "Schedule %s (%s) is due but its tag has no active items; skipping",
                schedule.pk,
                schedule.name,
            )
            schedule.last_run_at = now
            schedule.save(update_fields=["last_run_at"])
            continue

        webupdate = dispatch_fan_out(item_ids=item_ids)

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
