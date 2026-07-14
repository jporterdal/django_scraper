import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from . import parsers
from .fetcher import Fetcher, ResponseTooLargeError
from .matching import filter_results_for_item_source
from .models import FetchJob, ItemSource, SearchResult, WebUpdate

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


def _build_latest_snapshot_map(item_ids, exclude_update_id):
    """Latest stored (price, instock) per (item, source, title) from prior runs."""
    snapshots = {}
    if not item_ids:
        return snapshots
    rows = (
        SearchResult.objects.filter(item_id__in=item_ids)
        .exclude(update_id=exclude_update_id)
        .order_by("-update__timestamp", "-id")
        .values_list("item_id", "source_id", "title", "price", "instock")
    )
    for item_id, source_id, title, price, instock in rows:
        key = (item_id, source_id, title)
        if key not in snapshots:
            snapshots[key] = (price, instock)
    return snapshots


def _deduplicate_result_kwargs(kws, webupdate):
    """Filter parsed candidates against prior snapshots and within-batch duplicates."""
    if not kws:
        return [], 0, {}, {}

    item_ids = {kw["item"].pk for kw in kws}
    snapshots = _build_latest_snapshot_map(item_ids, exclude_update_id=webupdate.pk)
    batch_kept = {}
    filtered = []
    skipped = 0
    stored_by_job = defaultdict(int)
    skipped_by_job = defaultdict(int)

    for kw in kws:
        key = (kw["item"].pk, kw["source"].pk, kw["title"])
        candidate = (kw["price"], kw["instock"])
        job_id = kw["_fetch_job_id"]

        if snapshots.get(key) == candidate:
            skipped += 1
            skipped_by_job[job_id] += 1
            continue

        if key in batch_kept and batch_kept[key] == candidate:
            skipped += 1
            skipped_by_job[job_id] += 1
            continue

        filtered.append(kw)
        batch_kept[key] = candidate
        stored_by_job[job_id] += 1

    return filtered, skipped, stored_by_job, skipped_by_job


def _search_result_kwargs(kw):
    return {key: value for key, value in kw.items() if not key.startswith("_")}


def _run_parser_search(
    parser, fetcher, url, headers=None, max_pages=1, method="GET", body=None
):
    """Fetch one or more search pages and populate parser.results.

    Page 1 resets and parses via ``parser.parse_response``; pages ``2..max_pages``
    append via ``parser.parse_next_page``, following ``parser.next_page_url`` until it
    returns ``None`` or the page cap is reached. ``fetcher.wait()`` is honored between
    page requests for rate limiting. Pages already gathered are kept if a later page
    fails (non-200). When ``max_pages == 1`` this is byte-identical to a single fetch.

    POST sources issue ``fetcher.post`` on page 1 with ``json=body``; pages
    ``2..max_pages`` reuse the same URL with bodies from ``parser.next_page_body``
    when that method returns a dict (otherwise single-page).
    """
    parser.url = url
    current_body = body
    if method == "POST":
        response = fetcher.post(url, json=body, headers=headers)
    else:
        response = fetcher.get(url, headers=headers)

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
            fetcher.wait()
            next_response = fetcher.post(url, json=next_body, headers=headers)
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
            fetcher.wait()
            next_response = fetcher.get(next_url, headers=headers)
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


def run_web_update(items=None, fetcher=None, webupdate=None):
    """Fetch prices for active item sources with rate limiting and error logging.

    Accepts an optional existing ``webupdate`` (created if ``None``) so a
    background task can pre-create the row, then updates its progress fields
    (``status``/``total_searches``/``completed_searches``/``result_count``/
    ``error_count``) as the run proceeds. The returned ``WebUpdateStats``
    contract and the per-item-source fetch/pagination behavior are unchanged.
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
    error_count = 0
    fetch_job_count = 0
    completed_searches = 0
    kws = []
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

    try:
        for index, item_source in enumerate(item_sources):
            start = time.perf_counter()
            if index > 0:
                fetcher.wait()

            source = item_source.source
            item = item_source.item
            search_term = item.text
            search_url = ""
            log_ctx = {
                "source_key": source.key,
                "source_name": source.name,
                "item_id": item.pk,
                "search_term": search_term,
            }

            try:
                pinned_url = item_source.pinned_url
                if pinned_url:
                    search_url = pinned_url
                    fetch_method = "GET"
                    fetch_body = None
                else:
                    if not source.base_search_url:
                        duration_ms = int((time.perf_counter() - start) * 1000)
                        logger.error(
                            "Source %(source_key)r has no base_search_url configured",
                            log_ctx,
                        )
                        _record_fetch_job(
                            webupdate,
                            item,
                            source,
                            search_term,
                            search_url,
                            FetchJob.Status.CONFIG_ERROR,
                            error_message="Source has no base_search_url configured",
                            duration_ms=duration_ms,
                        )
                        fetch_job_count += 1
                        error_count += 1
                        continue

                    try:
                        search_url = source.build_search_url(
                            search_term, item_source.url_suffix
                        )
                    except ValueError as exc:
                        duration_ms = int((time.perf_counter() - start) * 1000)
                        logger.exception(
                            "Invalid base_search_url for %(source_key)r",
                            log_ctx,
                        )
                        _record_fetch_job(
                            webupdate,
                            item,
                            source,
                            search_term,
                            search_url,
                            FetchJob.Status.CONFIG_ERROR,
                            error_message=str(exc),
                            duration_ms=duration_ms,
                        )
                        fetch_job_count += 1
                        error_count += 1
                        continue

                    fetch_method = source.http_method
                    fetch_body = source.build_request_body(search_term)

                try:
                    parser_cls = parsers.sources[source.parser_key]
                except KeyError:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    logger.error(
                        "Unknown parser key %(source_key)r for source %(source_name)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.CONFIG_ERROR,
                        error_message=f"Unknown parser key {source.key!r}",
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    error_count += 1
                    continue

                try:
                    parser = parser_cls(term=search_term)
                except Exception as exc:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    logger.exception(
                        "Failed to create parser %(source_key)r for item %(item_id)s term %(search_term)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.PARSE_ERROR,
                        error_message=str(exc),
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    error_count += 1
                    continue
                
                headers = source.build_request_headers(search_term)
                
                try:
                    outcome = _run_parser_search(
                        parser,
                        fetcher,
                        search_url,
                        headers=headers,
                        max_pages=source.max_pages,
                        method=fetch_method,
                        body=fetch_body,
                    )
                except ResponseTooLargeError as exc:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    logger.warning(
                        "Oversized response for %(source_key)r item %(item_id)s term %(search_term)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.OVERSIZED,
                        error_message=str(exc),
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    error_count += 1
                    continue
                except (json.JSONDecodeError, ValueError) as exc:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    logger.exception(
                        "Invalid JSON for %(source_key)r item %(item_id)s term %(search_term)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.PARSE_ERROR,
                        error_message=f"Invalid JSON response: {exc}",
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    error_count += 1
                    continue
                except Exception as exc:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    logger.exception(
                        "Parse failed for %(source_key)r item %(item_id)s term %(search_term)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.PARSE_ERROR,
                        error_message=str(exc),
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    error_count += 1
                    continue

                duration_ms = int((time.perf_counter() - start) * 1000)

                if not outcome.ok:
                    logger.warning(
                        "No results fetched for %(source_key)r item %(item_id)s term %(search_term)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.BLOCKED
                        if outcome.blocked
                        else FetchJob.Status.HTTP_ERROR,
                        http_status=outcome.http_status,
                        error_message=outcome.error_message,
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    error_count += 1
                    continue

                if not parser.results:
                    logger.info(
                        "Empty result set for %(source_key)r item %(item_id)s term %(search_term)r",
                        log_ctx,
                    )
                    _record_fetch_job(
                        webupdate,
                        item,
                        source,
                        search_term,
                        search_url,
                        FetchJob.Status.EMPTY,
                        http_status=outcome.http_status,
                        duration_ms=duration_ms,
                    )
                    fetch_job_count += 1
                    continue

                logger.debug(f"Filtering {len(parser.results)} results for {source.key} {search_term}")

                matching_results = filter_results_for_item_source(
                    parser.results, item_source
                )
                fetch_job = _record_fetch_job(
                    webupdate,
                    item,
                    source,
                    search_term,
                    search_url,
                    FetchJob.Status.SUCCESS,
                    http_status=outcome.http_status,
                    duration_ms=duration_ms,
                    result_count=len(matching_results),
                )
                fetch_job_count += 1

                for result in matching_results:
                    kws.append({
                        "title": result["title"],
                        "price": result["price"] if result["instock"] else None,
                        "category": result["category"],
                        "search_term": search_term,
                        "item": item,
                        "instock": 1 if result["instock"] else 0,
                        "source": source,
                        "update": webupdate,
                        "_fetch_job_id": fetch_job.pk,
                        "_source_key": source.key,
                        "_item_id": item.pk,
                    })
            finally:
                # Persist incremental progress after each item-source so the
                # HTMX progress poll sees the run advance in real time.
                completed_searches += 1
                webupdate.completed_searches = completed_searches
                webupdate.error_count = error_count
                webupdate.result_count = len(kws)
                webupdate.save(
                    update_fields=[
                        "completed_searches",
                        "error_count",
                        "result_count",
                    ]
                )

        filtered_kws, skipped_duplicate_count, stored_by_job, skipped_by_job = (
            _deduplicate_result_kwargs(kws, webupdate)
        )

        for job_id, stored in stored_by_job.items():
            FetchJob.objects.filter(pk=job_id).update(stored_count=stored)

        fetch_jobs_by_id = {
            job.pk: job
            for job in FetchJob.objects.filter(webupdate=webupdate, pk__in=skipped_by_job)
        }
        for job_id, skipped_count in skipped_by_job.items():
            if skipped_count <= 0:
                continue
            job = fetch_jobs_by_id.get(job_id)
            if job is None:
                continue
            logger.info(
                "%(source_key)s/item=%(item_id)s: %(parsed)d parsed, "
                "%(stored)d stored (%(skipped)d unchanged)",
                {
                    "source_key": job.source_id,
                    "item_id": job.item_id,
                    "parsed": job.result_count,
                    "stored": stored_by_job.get(job_id, 0),
                    "skipped": skipped_count,
                },
            )

        if filtered_kws:
            SearchResult.objects.bulk_create(
                [SearchResult(**_search_result_kwargs(kw)) for kw in filtered_kws]
            )
    except Exception:
        webupdate.status = WebUpdate.Status.FAILED
        webupdate.error_count = error_count
        webupdate.result_count = len(kws)
        webupdate.save(
            update_fields=["status", "error_count", "result_count"]
        )
        raise

    stored_count = len(filtered_kws) if kws else 0
    webupdate.status = WebUpdate.Status.DONE
    webupdate.completed_searches = search_count
    webupdate.result_count = stored_count
    webupdate.skipped_duplicate_count = (
        skipped_duplicate_count if kws else 0
    )
    webupdate.error_count = error_count
    webupdate.save(
        update_fields=[
            "status",
            "completed_searches",
            "result_count",
            "skipped_duplicate_count",
            "error_count",
        ]
    )

    stats = WebUpdateStats(
        result_count=stored_count,
        error_count=error_count,
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
