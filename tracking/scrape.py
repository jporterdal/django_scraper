import json
import logging
import time
from dataclasses import dataclass, field

from . import parsers
from .fetcher import Fetcher, ResponseTooLargeError
from .matching import filter_results_for_item_source
from .models import FetchJob, ItemSource, SearchResult, WebUpdate
from .ratelimit import (
    DEFER,
    READY,
    SHORT_WAIT,
    PaceDecision,
    RateLimitPolicy,
    default_scope,
    get_budget_store,
    get_profile,
    reconcile_response,
    record_429,
    try_acquire,
)

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_LENGTH = 2000


@dataclass(frozen=True)
class FetchOutcome:
    ok: bool
    http_status: int | None
    error_message: str
    result_count: int
    blocked: bool = False


@dataclass(frozen=True)
class WebUpdateStats:
    result_count: int
    error_count: int
    search_count: int
    fetch_job_count: int = 0


@dataclass(frozen=True)
class UnitResult:
    """Outcome of one ``fetch_one_unit`` call (task 4.2).

    ``deferred=True`` means the unit did **not** terminalize: the caller
    (the Huey ``fetch_one`` task, or ``run_web_update``'s synchronous driver)
    owns the give-up (D10) vs requeue (D11) decision using ``decision``, the
    raw ``ratelimit.PaceDecision`` that triggered the defer.
    """

    deferred: bool
    fetch_job: object = None
    decision: PaceDecision | None = None


@dataclass
class UnitPacing:
    """Per-unit profiled-pacing context threaded through ``_run_parser_search``.

    ``cost_estimate`` starts at the profile's conservative default (D4) and is
    replaced by the learned cost after the first response that reports one, so
    later pages in the same unit's pagination (D14) call ``try_acquire`` with a
    realistic estimate.
    """

    store: object
    scope: str
    run_id: str
    policy: RateLimitPolicy
    profile: object
    cost_estimate: dict = field(default_factory=dict)


class _DeferSignal(Exception):
    """Internal control flow: the pacer (or a profiled 429) says Defer.

    Raised from inside ``_run_parser_search`` to unwind out of pagination
    immediately — before any partial page results are kept — per D3 (never
    send after a stale decision) and D14 (Defer discards the in-memory
    attempt and restarts from page 1 next time). Caught by ``fetch_one_unit``,
    which does not itself decide give-up vs requeue (D10); that stays with the
    Huey wrapper, which has ``attempt``/``webupdate`` context.
    """

    def __init__(self, decision):
        self.decision = decision
        super().__init__(f"ratelimit defer: reason={decision.reason} eta={decision.eta}")


def _truncate_error_message(message):
    if len(message) > MAX_ERROR_MESSAGE_LENGTH:
        return message[:MAX_ERROR_MESSAGE_LENGTH]
    return message


def _response_looks_blocked(response):
    """True when a JSON parser likely received an HTML block/challenge page."""
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type.lower():
        return True
    text = response.text if hasattr(response, "text") else ""
    stripped = text.lstrip()
    lower_prefix = stripped[:20].lower()
    return lower_prefix.startswith("<!doctype") or lower_prefix.startswith("<html")


def _record_fetch_job(
    webupdate,
    item,
    source,
    search_term,
    search_url,
    status,
    http_status=None,
    error_message="",
    duration_ms=0,
    result_count=0,
):
    return FetchJob.objects.create(
        webupdate=webupdate,
        item=item,
        source=source,
        search_term=search_term,
        search_url=search_url,
        status=status,
        http_status=http_status,
        error_message=_truncate_error_message(error_message),
        duration_ms=duration_ms,
        result_count=result_count,
    )


def _build_unit_snapshot_map(item, source, exclude_update_id):
    """Latest stored ``(price, instock)`` per title for one (item, source) pair.

    Scoped to a single unit (D12/task 8.2) rather than the old run-level batch
    query across every item in the run.
    """
    rows = (
        SearchResult.objects.filter(item_id=item.pk, source_id=source.pk)
        .exclude(update_id=exclude_update_id)
        .order_by("-update__timestamp", "-id")
        .values_list("title", "price", "instock")
    )
    snapshot = {}
    for title, price, instock in rows:
        if title not in snapshot:
            snapshot[title] = (price, instock)
    return snapshot


def _dedupe_unit_candidates(candidates, item, source, webupdate):
    """Filter one unit's parsed candidates against prior + within-unit dupes (D12).

    Same predicates as the old run-level ``_deduplicate_result_kwargs``: key
    ``(item, source, title)``, compare ``(price, instock)``. Cross-unit
    collision cannot happen under fan-out because ``ItemSource`` is unique on
    ``(item, source)``, so each unit owns a disjoint dedup key space (D12) —
    only within this one candidate list can a duplicate title appear.
    """
    if not candidates:
        return [], 0

    snapshot = _build_unit_snapshot_map(item, source, exclude_update_id=webupdate.pk)
    kept = {}
    survivors = []
    skipped = 0

    for candidate in candidates:
        title = candidate["title"]
        key = (candidate["price"], candidate["instock"])

        if snapshot.get(title) == key:
            skipped += 1
            continue
        if kept.get(title) == key:
            skipped += 1
            continue

        survivors.append(candidate)
        kept[title] = key

    return survivors, skipped


def terminalize(
    webupdate,
    item,
    source,
    search_term,
    search_url,
    status,
    http_status=None,
    error_message="",
    duration_ms=0,
    candidates=None,
):
    """Idempotent per-unit terminal step (D9 barrier + D12 dedup/store).

    Records exactly one ``FetchJob`` for ``(webupdate, item, source)`` (DB
    unique constraint backstops the pre-check — D11), dedupes and stores
    ``candidates`` (only meaningful when ``status == SUCCESS``), atomically
    bumps ``WebUpdate`` progress counters with ``F()``, and — when this is the
    Nth terminal FetchJob for the run — compare-and-sets ``RUNNING`` ->
    ``DONE`` (D9, no sweeper). Returns the created ``FetchJob``, or the
    pre-existing one when a duplicate delivery makes this a no-op: no counters
    are touched in that case.
    """
    from django.db import IntegrityError
    from django.db.models import F

    existing = FetchJob.objects.filter(
        webupdate=webupdate, item=item, source=source
    ).first()
    if existing is not None:
        logger.info(
            "terminalize no-op: FetchJob already exists webupdate=%s item=%s source=%s",
            webupdate.pk,
            item.pk,
            source.pk,
        )
        return existing

    is_error = status in (
        FetchJob.Status.HTTP_ERROR,
        FetchJob.Status.PARSE_ERROR,
        FetchJob.Status.CONFIG_ERROR,
        FetchJob.Status.OVERSIZED,
        FetchJob.Status.BLOCKED,
        FetchJob.Status.GIVE_UP,
    )

    survivors, skipped_count = [], 0
    if status == FetchJob.Status.SUCCESS and candidates:
        survivors, skipped_count = _dedupe_unit_candidates(
            candidates, item, source, webupdate
        )
    stored_count = len(survivors)

    try:
        fetch_job = _record_fetch_job(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            status,
            http_status=http_status,
            error_message=error_message,
            duration_ms=duration_ms,
            result_count=len(candidates) if candidates else 0,
        )
    except IntegrityError:
        # Lost a race against another delivery of the same unit — the Redis
        # NX lock (D11) should make this rare, but the DB constraint is the
        # final backstop, so treat it the same as the pre-check no-op.
        existing = FetchJob.objects.get(webupdate=webupdate, item=item, source=source)
        logger.info(
            "terminalize lost race, treating as no-op: FetchJob %s", existing.pk
        )
        return existing

    if survivors:
        SearchResult.objects.bulk_create(
            [
                SearchResult(
                    title=candidate["title"],
                    price=candidate["price"],
                    category=candidate["category"],
                    product_line=candidate.get("product_line", ""),
                    search_term=search_term,
                    item=item,
                    instock=candidate["instock"],
                    source=source,
                    update=webupdate,
                )
                for candidate in survivors
            ]
        )
        FetchJob.objects.filter(pk=fetch_job.pk).update(stored_count=stored_count)
        fetch_job.stored_count = stored_count

        logger.info(
            "%s/item=%s: %d parsed, %d stored (%d unchanged)",
            source.key,
            item.pk,
            fetch_job.result_count,
            stored_count,
            skipped_count,
        )
    elif skipped_count:
        logger.info(
            "%s/item=%s: %d parsed, 0 stored (%d unchanged)",
            source.key,
            item.pk,
            fetch_job.result_count,
            skipped_count,
        )

    counter_updates = {"completed_searches": F("completed_searches") + 1}
    if stored_count:
        counter_updates["result_count"] = F("result_count") + stored_count
    if skipped_count:
        counter_updates["skipped_duplicate_count"] = (
            F("skipped_duplicate_count") + skipped_count
        )
    if is_error:
        counter_updates["error_count"] = F("error_count") + 1

    WebUpdate.objects.filter(pk=webupdate.pk).update(**counter_updates)
    WebUpdate.objects.filter(pk=webupdate.pk, status=WebUpdate.Status.PENDING).update(
        status=WebUpdate.Status.RUNNING
    )

    finished = FetchJob.objects.filter(webupdate_id=webupdate.pk).count()
    if finished >= webupdate.total_searches:
        flipped = WebUpdate.objects.filter(
            pk=webupdate.pk, status=WebUpdate.Status.RUNNING
        ).update(status=WebUpdate.Status.DONE)
        if flipped:
            logger.info(
                "WebUpdate %s: last unit terminalized (%d/%d), marked DONE",
                webupdate.pk,
                finished,
                webupdate.total_searches,
            )

    return fetch_job


def _should_give_up(webupdate, attempt, policy=None, now=None):
    """Give-up predicate (D10): ``attempt`` cap OR run wall-clock age."""
    policy = policy or RateLimitPolicy.from_settings()
    now = now if now is not None else time.time()
    if attempt >= policy.max_defer_attempts:
        return True
    age_seconds = now - webupdate.timestamp.timestamp()
    return age_seconds >= policy.max_run_wall_clock_seconds


def _run_parser_search(
    parser,
    fetcher,
    url,
    headers=None,
    max_pages=1,
    method="GET",
    body=None,
    pacing=None,
):
    """Fetch one or more search pages and populate parser.results.

    Page 1 resets and parses via ``parser.parse_response``; pages ``2..max_pages``
    append via ``parser.parse_next_page``, following ``parser.next_page_url`` until it
    returns ``None`` or the page cap is reached. Pages already gathered are kept if a
    later page fails with a plain (non rate-limit) non-200. When ``max_pages == 1``
    this is byte-identical to a single fetch.

    POST sources issue ``fetcher.post`` on page 1 with ``json=body``; pages
    ``2..max_pages`` reuse the same URL with bodies from ``parser.next_page_body``
    when that method returns a dict (otherwise single-page).

    ``pacing`` is ``None`` for the default/``none`` rate-limit profile: pages are
    paced with ``fetcher.wait()`` between requests exactly as before, no budget
    store involved (D1/D4). When ``pacing`` is a ``UnitPacing``, every send —
    including page 1 — goes through ``try_acquire`` first (D3/D14): Ready sends,
    ShortWait sleeps and re-checks, Defer/give-up unwinds via ``_DeferSignal``
    before anything is parsed or stored. A profiled response's HTTP 429 is
    surfaced the same way (D6) rather than becoming a ``blocked`` FetchOutcome —
    it stores the Retry-After cooldown and also raises ``_DeferSignal``.
    """
    parser.url = url
    profiled = pacing is not None

    def _gate():
        costs = pacing.cost_estimate or {"request": 1}
        while True:
            decision = try_acquire(
                pacing.store, pacing.scope, costs, pacing.run_id, pacing.policy
            )
            if decision.kind == READY:
                if decision.fallback:
                    # Cold start (task 6.2): no meters known yet for this
                    # scope, so nothing was actually paced by the reservation
                    # above — fall back to the same fixed delay the none/HTML
                    # path uses until a response teaches us real meters.
                    fetcher.wait()
                return
            if decision.kind == SHORT_WAIT:
                time.sleep(decision.wait_seconds)
                continue
            raise _DeferSignal(decision)

    def _send(http_method, send_url, send_body):
        if not profiled:
            if http_method == "POST":
                return fetcher.post(send_url, json=send_body, headers=headers)
            return fetcher.get(send_url, headers=headers)

        _gate()
        if http_method == "POST":
            response = fetcher.post(
                send_url, json=send_body, headers=headers, profiled=True
            )
        else:
            response = fetcher.get(send_url, headers=headers, profiled=True)

        if response.status_code == 429:
            until_ts = record_429(pacing.store, pacing.scope, response)
            raise _DeferSignal(PaceDecision(kind=DEFER, eta=until_ts, reason="retry_after"))

        learned_cost = reconcile_response(pacing.store, pacing.scope, pacing.profile, response)
        if learned_cost:
            pacing.cost_estimate = dict(learned_cost)
        return response

    current_body = body
    response = _send(method, url, body if method == "POST" else None)

    if response.status_code != 200:
        return FetchOutcome(
            ok=False,
            http_status=response.status_code,
            error_message=f"HTTP {response.status_code}",
            result_count=0,
            blocked=response.status_code in (403, 429),
        )

    try:
        parser.parse_response(response)
    except (json.JSONDecodeError, ValueError) as exc:
        if _response_looks_blocked(response):
            return FetchOutcome(
                ok=False,
                http_status=response.status_code,
                error_message=f"Blocked response (expected JSON): {exc}",
                result_count=0,
                blocked=True,
            )
        raise

    current_url = url
    for page in range(2, max_pages + 1):
        if method == "POST":
            next_body = parser.next_page_body(response, current_body, page)
            if next_body is None:
                break
            if not profiled:
                fetcher.wait()
            next_response = _send("POST", url, next_body)
            if next_response.status_code != 200:
                logger.warning(
                    "POST pagination stopped at page %d (%s): HTTP %s — keeping earlier pages",
                    page,
                    url,
                    next_response.status_code,
                )
                break
            parser.parse_next_page(next_response)
            current_body = next_body
            response = next_response
        else:
            next_url = parser.next_page_url(response, current_url, page)
            if next_url is None:
                break
            if not profiled:
                fetcher.wait()
            next_response = _send("GET", next_url, None)
            if next_response.status_code != 200:
                logger.warning(
                    "Pagination stopped at page %d (%s): HTTP %s — keeping earlier pages",
                    page,
                    next_url,
                    next_response.status_code,
                )
                break
            parser.parse_next_page(next_response)
            current_url = next_url
            response = next_response

    return FetchOutcome(
        ok=True,
        http_status=200,
        error_message="",
        result_count=len(parser.results),
    )


def fetch_one_unit(webupdate, item_source, fetcher=None, attempt=0, run_id=None, policy=None):
    """Fetch + parse + dedup + terminalize for exactly one ``ItemSource`` (D8/task 4.2).

    Shared by the Huey ``fetch_one`` task wrapper (``tracking.tasks``) and the
    synchronous ``run_web_update`` driver below. Performs no Huey scheduling
    itself — a ``UnitResult(deferred=True, decision=...)`` return means the
    caller must apply the give-up (D10) vs requeue (D11) decision; this keeps
    the function testable without a worker.
    """
    if fetcher is None:
        fetcher = Fetcher.from_settings()
    policy = policy or RateLimitPolicy.from_settings()
    run_id = run_id if run_id is not None else str(webupdate.pk)

    source = item_source.source
    item = item_source.item
    search_term = item.text
    search_url = ""
    start = time.perf_counter()
    log_ctx = {
        "source_key": source.key,
        "source_name": source.name,
        "item_id": item.pk,
        "search_term": search_term,
    }

    try:
        profile = get_profile(source.rate_limit_profile)
    except KeyError:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "Unknown rate_limit_profile %r for source %r",
            source.rate_limit_profile,
            source.key,
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.CONFIG_ERROR,
            error_message=f"Unknown rate_limit_profile {source.rate_limit_profile!r}",
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)

    pacing = None
    if profile is not None:
        pacing = UnitPacing(
            store=get_budget_store(),
            scope=default_scope(source),
            run_id=run_id,
            policy=policy,
            profile=profile,
            cost_estimate=dict(profile.default_cost) if profile.default_cost else {},
        )

    pinned_url = item_source.pinned_url
    if pinned_url:
        search_url = pinned_url
        fetch_method = "GET"
        fetch_body = None
    else:
        if not source.base_search_url:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error("Source %(source_key)r has no base_search_url configured", log_ctx)
            fetch_job = terminalize(
                webupdate,
                item,
                source,
                search_term,
                search_url,
                FetchJob.Status.CONFIG_ERROR,
                error_message="Source has no base_search_url configured",
                duration_ms=duration_ms,
            )
            return UnitResult(deferred=False, fetch_job=fetch_job)

        try:
            search_url = source.build_search_url(search_term, item_source.url_suffix)
        except ValueError as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception("Invalid base_search_url for %(source_key)r", log_ctx)
            fetch_job = terminalize(
                webupdate,
                item,
                source,
                search_term,
                search_url,
                FetchJob.Status.CONFIG_ERROR,
                error_message=str(exc),
                duration_ms=duration_ms,
            )
            return UnitResult(deferred=False, fetch_job=fetch_job)

        fetch_method = source.http_method
        fetch_body = source.build_request_body(search_term)

    try:
        parser_cls = parsers.sources[source.parser_key]
    except KeyError:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "Unknown parser key %(source_key)r for source %(source_name)r", log_ctx
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.CONFIG_ERROR,
            error_message=f"Unknown parser key {source.key!r}",
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)

    try:
        parser = parser_cls(term=search_term)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "Failed to create parser %(source_key)r for item %(item_id)s term %(search_term)r",
            log_ctx,
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.PARSE_ERROR,
            error_message=str(exc),
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)

    if isinstance(parser, parsers.JSONSearchParser):
        parser.expected_product_line = item.expected_product_line
        parser.expected_category = item.expected_category
        parser.source = source

    headers = source.build_request_headers(search_term)

    if pacing is None:
        # HTML / none profile: fixed-delay pacing only (D1/D4/task 6.1/6.2).
        fetcher.wait()

    try:
        outcome = _run_parser_search(
            parser,
            fetcher,
            search_url,
            headers=headers,
            max_pages=source.max_pages,
            method=fetch_method,
            body=fetch_body,
            pacing=pacing,
        )
    except _DeferSignal as defer:
        return UnitResult(deferred=True, decision=defer.decision)
    except ResponseTooLargeError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.warning(
            "Oversized response for %(source_key)r item %(item_id)s term %(search_term)r",
            log_ctx,
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.OVERSIZED,
            error_message=str(exc),
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)
    except (json.JSONDecodeError, ValueError) as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "Invalid JSON for %(source_key)r item %(item_id)s term %(search_term)r", log_ctx
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.PARSE_ERROR,
            error_message=f"Invalid JSON response: {exc}",
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "Parse failed for %(source_key)r item %(item_id)s term %(search_term)r", log_ctx
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.PARSE_ERROR,
            error_message=str(exc),
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)

    duration_ms = int((time.perf_counter() - start) * 1000)

    if not outcome.ok:
        logger.warning(
            "No results fetched for %(source_key)r item %(item_id)s term %(search_term)r", log_ctx
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.BLOCKED if outcome.blocked else FetchJob.Status.HTTP_ERROR,
            http_status=outcome.http_status,
            error_message=outcome.error_message,
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)

    if not parser.results:
        logger.info(
            "Empty result set for %(source_key)r item %(item_id)s term %(search_term)r", log_ctx
        )
        fetch_job = terminalize(
            webupdate,
            item,
            source,
            search_term,
            search_url,
            FetchJob.Status.EMPTY,
            http_status=outcome.http_status,
            duration_ms=duration_ms,
        )
        return UnitResult(deferred=False, fetch_job=fetch_job)

    logger.debug(f"Filtering {len(parser.results)} results for {source.key} {search_term}")
    matching_results = filter_results_for_item_source(parser.results, item_source)
    candidates = [
        {
            "title": result["title"],
            "price": result["price"] if result["instock"] else None,
            "category": result["category"],
            "product_line": result.get("product_line", ""),
            "instock": 1 if result["instock"] else 0,
        }
        for result in matching_results
    ]
    fetch_job = terminalize(
        webupdate,
        item,
        source,
        search_term,
        search_url,
        FetchJob.Status.SUCCESS,
        http_status=outcome.http_status,
        duration_ms=duration_ms,
        candidates=candidates,
    )
    return UnitResult(deferred=False, fetch_job=fetch_job)


def run_web_update(items=None, fetcher=None, webupdate=None):
    """Synchronous multi-unit driver — tests and ``SearchResult.update_from_web``.

    Loops every active ``ItemSource`` and drives ``fetch_one_unit`` for each,
    in-process, sharing one ``fetcher`` — no Huey, no idempotency lock, no
    requeue. **Production dispatch no longer uses this path**: see
    ``tracking.tasks.dispatch_fan_out`` / ``fetch_one`` for the real
    one-Huey-task-per-ItemSource fan-out (D8), which is what schedules and the
    manual "Update" view use. This driver exists so single-process callers
    (tests, ``SearchResult.update_from_web``) still have a synchronous
    contract to call, without a second implementation of "what a unit does" —
    both paths call ``fetch_one_unit``.

    A Defer decision (rate-limit pacing on a profiled source) is a hard stop
    here: there is no Huey to requeue through in a synchronous call, so the
    unit terminalizes immediately as ``give_up`` rather than sleeping or
    looping in-process.
    """
    if fetcher is None:
        fetcher = Fetcher.from_settings()

    item_sources = ItemSource.objects.filter(item__active=True).select_related(
        "item", "source"
    )
    if items is not None:
        item_sources = item_sources.filter(item__in=items)
    item_sources = list(item_sources)
    search_count = len(item_sources)

    if webupdate is None:
        webupdate = WebUpdate.objects.create()

    webupdate.status = WebUpdate.Status.RUNNING
    webupdate.total_searches = search_count
    webupdate.completed_searches = 0
    webupdate.result_count = 0
    webupdate.error_count = 0
    webupdate.skipped_duplicate_count = 0
    webupdate.save(
        update_fields=[
            "status",
            "total_searches",
            "completed_searches",
            "result_count",
            "error_count",
            "skipped_duplicate_count",
        ]
    )

    logger.info("Starting web update for %d item source(s)", search_count)

    run_id = str(webupdate.pk)
    policy = RateLimitPolicy.from_settings()
    fetch_job_count = 0

    try:
        for item_source in item_sources:
            result = fetch_one_unit(
                webupdate, item_source, fetcher=fetcher, run_id=run_id, policy=policy
            )
            if result.deferred:
                terminalize(
                    webupdate,
                    item_source.item,
                    item_source.source,
                    item_source.item.text,
                    "",
                    FetchJob.Status.GIVE_UP,
                    error_message=(
                        f"Deferred (reason={result.decision.reason}); "
                        "run_web_update has no synchronous requeue path"
                    ),
                )
            fetch_job_count += 1
    except Exception:
        WebUpdate.objects.filter(pk=webupdate.pk).update(status=WebUpdate.Status.FAILED)
        webupdate.status = WebUpdate.Status.FAILED
        raise

    webupdate.refresh_from_db()
    if search_count == 0 and webupdate.status != WebUpdate.Status.DONE:
        # No units to fan out to, so terminalize never ran the DONE CAS.
        webupdate.status = WebUpdate.Status.DONE
        webupdate.save(update_fields=["status"])
    elif webupdate.status == WebUpdate.Status.RUNNING:
        # Defensive: every unit above terminalizes (success, error, or
        # give-up), so the CAS in the last terminalize should already have
        # flipped this to DONE. Surface it loudly if it didn't.
        logger.warning(
            "WebUpdate %s finished all %d unit(s) but is still RUNNING; forcing DONE",
            webupdate.pk,
            search_count,
        )
        webupdate.status = WebUpdate.Status.DONE
        webupdate.save(update_fields=["status"])

    stats = WebUpdateStats(
        result_count=webupdate.result_count,
        error_count=webupdate.error_count,
        search_count=search_count,
        fetch_job_count=fetch_job_count,
    )
    logger.info(
        "Web update finished: %d result(s) stored, %d unchanged skipped, "
        "%d error(s), %d search(es) attempted",
        stats.result_count,
        webupdate.skipped_duplicate_count,
        stats.error_count,
        stats.search_count,
    )
    return stats
