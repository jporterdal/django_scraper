import json
import logging
import time
from dataclasses import dataclass

from . import parsers
from .fetcher import Fetcher, ResponseTooLargeError
from .models import FetchJob, ItemSource, SearchResult, WebUpdate

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_LENGTH = 2000


@dataclass(frozen=True)
class FetchOutcome:
    ok: bool
    http_status: int | None
    error_message: str
    result_count: int


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
    FetchJob.objects.create(
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


def _run_parser_search(parser, fetcher, url, headers=None, max_pages=1):
    """Fetch one or more search pages and populate parser.results.

    Page 1 resets and parses via ``parser.parse_response``; pages ``2..max_pages``
    append via ``parser.parse_next_page``, following ``parser.next_page_url`` until it
    returns ``None`` or the page cap is reached. ``fetcher.wait()`` is honored between
    page requests for rate limiting. Pages already gathered are kept if a later page
    fails (non-200). When ``max_pages == 1`` this is byte-identical to a single fetch.
    """
    parser.url = url
    response = fetcher.get(url, headers=headers)

    if response.status_code != 200:
        return FetchOutcome(
            ok=False,
            http_status=response.status_code,
            error_message=f"HTTP {response.status_code}",
            result_count=0,
        )

    parser.parse_response(response)

    current_url = url
    for page in range(2, max_pages + 1):
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
    webupdate.save(
        update_fields=[
            "status",
            "total_searches",
            "completed_searches",
            "result_count",
            "error_count",
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

                headers = source.request_headers or None

                try:
                    outcome = _run_parser_search(
                        parser,
                        fetcher,
                        search_url,
                        headers=headers,
                        max_pages=source.max_pages,
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
                        FetchJob.Status.HTTP_ERROR,
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

                _record_fetch_job(
                    webupdate,
                    item,
                    source,
                    search_term,
                    search_url,
                    FetchJob.Status.SUCCESS,
                    http_status=outcome.http_status,
                    duration_ms=duration_ms,
                    result_count=len(parser.results),
                )
                fetch_job_count += 1

                for result in parser.results:
                    kws.append({
                        "title": result["title"],
                        "price": result["price"],
                        "category": result["category"],
                        "search_term": search_term,
                        "item": item,
                        "instock": 1 if result["instock"] else 0,
                        "source": source,
                        "update": webupdate,
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

        if kws:
            SearchResult.objects.bulk_create([SearchResult(**kw) for kw in kws])
    except Exception:
        webupdate.status = WebUpdate.Status.FAILED
        webupdate.error_count = error_count
        webupdate.result_count = len(kws)
        webupdate.save(
            update_fields=["status", "error_count", "result_count"]
        )
        raise

    webupdate.status = WebUpdate.Status.DONE
    webupdate.completed_searches = search_count
    webupdate.result_count = len(kws)
    webupdate.error_count = error_count
    webupdate.save(
        update_fields=[
            "status",
            "completed_searches",
            "result_count",
            "error_count",
        ]
    )

    stats = WebUpdateStats(
        result_count=len(kws),
        error_count=error_count,
        search_count=search_count,
        fetch_job_count=fetch_job_count,
    )
    logger.info(
        "Web update finished: %d result(s) stored, %d error(s), %d search(es) attempted",
        stats.result_count,
        stats.error_count,
        stats.search_count,
    )
    return stats
