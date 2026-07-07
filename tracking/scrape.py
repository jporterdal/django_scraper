import logging
import time
from dataclasses import dataclass

from . import parsers
from .fetcher import Fetcher
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


def _run_parser_search(parser, fetcher, url):
    """Fetch a search page and populate parser.results without using SearchParser.search()."""
    parser._init_vars()
    parser.url = url
    response = fetcher.get(url)

    if response.status_code != 200:
        return FetchOutcome(
            ok=False,
            http_status=response.status_code,
            error_message=f"HTTP {response.status_code}",
            result_count=0,
        )

    parser.feed(response.text)
    return FetchOutcome(
        ok=True,
        http_status=200,
        error_message="",
        result_count=len(parser.results),
    )


def run_web_update(items=None, fetcher=None):
    """Fetch prices for active item sources with rate limiting and error logging."""
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
    kws = []
    webupdate = WebUpdate.objects.create()

    logger.info("Starting web update for %d item source(s)", search_count)

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
            search_url = source.build_search_url(search_term, item_source.url_suffix)
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
            parser_cls = parsers.sources[source.key]
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

        try:
            outcome = _run_parser_search(parser, fetcher, search_url)
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

    if kws:
        SearchResult.objects.bulk_create([SearchResult(**kw) for kw in kws])

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
