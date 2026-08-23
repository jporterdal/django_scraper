import copy
from collections import Counter, defaultdict
from datetime import timedelta
from urllib.parse import quote_plus

from django.db import models
from django.db.models import Count, Q
from django.utils import timezone


CC_DEFAULT_SEARCH_URL = (
    "https://www.canadacomputers.com/en/search?s={term}&pickup=62"
)


class Source(models.Model):
    name = models.CharField(
        null=False,
        blank=False,
        verbose_name="User-given name for this search source"
    )

    key = models.CharField(
        max_length=20,
        primary_key=True,
        verbose_name="Short abbreviation identifying this source (user-facing; e.g. 'cc')",
    )

    parser_key = models.CharField(
        max_length=20,
        blank=False,
        verbose_name="Parser registry key selecting which parser handles this source (e.g. 'shopify', 'storepass')",
    )

    rate_limit_profile = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name=(
            "Rate-limit profile registry key selecting how this source's budget "
            "is extracted and paced (e.g. 'ietf', 'x-ratelimit', 'graphql_cost'); "
            "blank/'none' keeps the fixed-delay pacing all sources use today"
        ),
    )

    base_search_url = models.CharField(
        max_length=1000,
        blank=False,
        verbose_name="Search URL template; use {term} for the URL-encoded query string",
    )

    request_headers = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Extra HTTP headers sent with search requests (e.g. Accept/Origin/Referer)",
    )

    # Payload minimization: to keep responses small enough to stay under
    # settings.SCRAPE_MAX_RESPONSE_BYTES, operators should reduce the payload at
    # the source. Bake a ``limit``/``pageSize`` (or equivalent) query param into
    # ``base_search_url`` and/or set this ``page_size`` hint. There is no
    # automatic injection of ``page_size`` into request URLs; it is an operator
    # hint only. Any future auto-injection should stay opt-in and non-breaking.
    page_size = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Optional results-per-page hint for paginated APIs",
    )

    max_pages = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=(
            "Max pages to fetch per search (1 = single page). GET sources paginate "
            "via parser ``next_page_url``; POST sources require parser "
            "``next_page_body`` support."
        ),
    )

    class HttpMethod(models.TextChoices):
        GET = "GET", "GET"
        POST = "POST", "POST"

    http_method = models.CharField(
        max_length=4,
        choices=HttpMethod.choices,
        default=HttpMethod.GET,
        verbose_name="HTTP method for search requests",
    )

    request_body_template = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="POST request body template; use {term} in string leaves for the search term",
    )

    def build_request_body(self, term):
        if self.http_method != self.HttpMethod.POST:
            return None
        if not self.request_body_template:
            return None
        return _inject_term(
            copy.deepcopy(self.request_body_template), term, lambda value: value
        )

    def build_request_headers(self, term):
        if not self.request_headers:
            return None
        return _inject_term(
            copy.deepcopy(self.request_headers), term, quote_plus
        )

    def build_search_url(self, term, url_suffix=""):
        if self.http_method == self.HttpMethod.POST:
            if "{term}" in self.base_search_url:
                url = self.base_search_url.format(term=quote_plus(term))
            else:
                url = self.base_search_url
        else:
            if "{term}" not in self.base_search_url:
                raise ValueError(
                    f"Source {self.key!r} base_search_url must contain '{{term}}'"
                )
            url = self.base_search_url.format(term=quote_plus(term))
        if url_suffix:
            if url_suffix.startswith(("&", "?")):
                url += url_suffix
            else:
                url += "&" + url_suffix
        return url


def _inject_term(obj, term, encode):
    """Replace ``{term}`` in every string leaf of a JSON-like structure.

    ``encode`` prepares the substitution value (raw for POST bodies, URL-encoded
    for headers and search URLs).
    """
    replacement = encode(term)
    if isinstance(obj, dict):
        return {
            key: _inject_term(value, term, encode) for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_inject_term(value, term, encode) for value in obj]
    if isinstance(obj, str):
        return obj.replace("{term}", replacement)
    return obj


class SearchableItem(models.Model):
    text = models.CharField(
        max_length=125,
        null=False,
        blank=False,
        verbose_name="Text to be used for identifying and when searching for item"
    )

    class Priority(models.IntegerChoices):
        # Tier system, lower integer value is higher.
        S = 0,
        A = 1,
        B = 2,
        C = 3,

    priority = models.IntegerField(
        choices=Priority.choices,
        default=Priority.B,
        verbose_name="Priority for this item's acquisition",
        blank=False,
        null=False,
    )

    active = models.BooleanField(
        default=True,
        blank=False,
        null=False,
        verbose_name="Indicate whether item should be actively updated or not"
    )

    tags = models.ManyToManyField(
        "Tag",
        blank=True,
        related_name="items",
        verbose_name="Tags for grouping and filtering items",
    )

    expected_product_line = models.JSONField(
        default=list,
        blank=True,
        verbose_name=(
            "Expected product line(s), each a {'value': str, 'source': str|None} "
            "entry (e.g. {'value': 'Magic', 'source': None}, or one entry per "
            "vendor when vendors word it differently) — a result from vendor V "
            "must contain at least one of V's applicable values (V's own tagged "
            "entries plus every source=None/manual entry) to disambiguate this "
            "item from a same-titled item in an unrelated product line. A vendor "
            "with no applicable entries has this check disabled for its own "
            "rows. See expected-value-vendor-provenance design.md."
        ),
    )

    expected_category = models.JSONField(
        default=list,
        blank=True,
        verbose_name=(
            "Expected category/set value(s), each a {'value': str, 'source': "
            "str|None} entry (e.g. a specific MTG set, possibly spelled "
            "differently per vendor) — a result from vendor V must contain at "
            "least one of V's applicable values (V's own tagged entries plus "
            "every source=None/manual entry) to narrow results beyond "
            "product-line disambiguation. Independent of expected product "
            "line. A vendor with no applicable entries has this check disabled "
            "for its own rows. See expected-value-vendor-provenance design.md."
        ),
    )

    def expected_values_for_source(self, field_name, source_key):
        """Applicable ``expected_<field_name>`` values for one vendor's rows.

        Returns that vendor's own tagged entries' values plus every
        ``source: null`` (manual) entry's value — the per-vendor pruning
        used at the scrape handoff (see design.md Decision 2). ``field_name``
        is ``"product_line"`` or ``"category"``.
        """
        entries = getattr(self, f"expected_{field_name}")
        return [
            entry["value"]
            for entry in entries
            if entry["source"] == source_key or entry["source"] is None
        ]

    metadata_provider_key = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name=(
            "Metadata provider registry key selecting which external metadata "
            "provider enriches this item (e.g. 'scryfall'); blank disables "
            "metadata enrichment"
        ),
    )


class Tag(models.Model):
    name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Tag name",
    )
    color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        verbose_name="Optional badge color (hex, e.g. #3498db)",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ItemSource(models.Model):
    item = models.ForeignKey(
        SearchableItem,
        on_delete=models.CASCADE,
        verbose_name="Item for this search source",
    )
    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        verbose_name="Eligible search source for this item",
    )
    url_suffix = models.CharField(
        max_length=250,
        blank=True,
        default="",
        verbose_name="Optional extra query string appended to the source search URL",
    )
    title_include_patterns = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Regex patterns; result title must match at least one if non-empty",
    )
    title_exclude_patterns = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Regex patterns; matching titles are excluded",
    )
    pinned_url = models.URLField(
        max_length=1000,
        blank=True,
        default="",
        verbose_name="Pinned result URL — fetch this directly instead of running a search",
    )

    class Meta:
        unique_together = [("item", "source")]


class ItemMetadata(models.Model):
    """Fetched/resolution state for one item's external metadata (see item-metadata-enrichment).

    One-to-one with ``SearchableItem`` — separate from ``metadata_provider_key``
    (operator configuration) for the same reason ``SearchResult``/``WebUpdate``
    live apart from ``Source``: configuration and system-fetched state are
    different concerns with different writers. ``payload`` is opaque to core
    application code, interpreted only by the owning provider's ``to_display()``;
    when ``status`` is ``needs_review`` it additionally holds a ``"candidates"``
    list of ``{"external_id", "payload"}`` dicts for the disambiguation UI.
    """

    class Status(models.TextChoices):
        UNFETCHED = "unfetched", "Unfetched"
        PENDING = "pending", "Pending"
        MATCHED = "matched", "Matched"
        NEEDS_REVIEW = "needs_review", "Needs review"
        NO_MATCH = "no_match", "No match"
        ERROR = "error", "Error"

    item = models.OneToOneField(
        SearchableItem,
        on_delete=models.CASCADE,
        related_name="metadata",
        verbose_name="Item this fetched metadata belongs to",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNFETCHED,
        verbose_name="Current metadata resolution/fetch status",
    )
    external_id = models.CharField(
        max_length=250,
        blank=True,
        default="",
        verbose_name="Provider's resolved external identifier for the current match, if any",
    )
    pinned_external_id = models.CharField(
        max_length=250,
        blank=True,
        default="",
        verbose_name=(
            "Operator-set manual override identifier — sticky and authoritative "
            "once set; exempts the item from the text-change re-fetch trigger"
        ),
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Opaque raw data as returned by the provider",
    )
    fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="When this record was last updated by a fetch",
    )


class MetadataFetchRequest(models.Model):
    """Queue row backing the bounded-rate metadata refresh drain (see item-metadata-enrichment).

    Populated only by ``tracking.metadata.request_metadata_refresh`` — the
    single shared enqueue entrypoint — and drained by a periodic Huey task
    (``tracking.tasks.drain_metadata_fetch_queue``) at a bounded rate per wake,
    independent of the vendor rate-limit/budget subsystem.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DONE = "done", "Done"

    item = models.ForeignKey(
        SearchableItem,
        on_delete=models.CASCADE,
        verbose_name="Item to (re-)fetch metadata for",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    class Meta:
        indexes = [
            models.Index(fields=["status", "requested_at"]),
        ]


class WebUpdate(models.Model):
    class Meta:
        indexes = [
            models.Index(fields=["timestamp"]),
        ]

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    timestamp = models.DateTimeField(
        auto_now_add=True,
    )

    # Progress tracking for background (Huey) runs, polled by the HTMX progress
    # partial. ``total_searches`` is set once the run starts; the per-item-source
    # counters are incremented as the run proceeds so polling sees live progress.
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Background run status",
    )
    total_searches = models.PositiveIntegerField(
        default=0,
        verbose_name="Total item-source searches planned for this run",
    )
    completed_searches = models.PositiveIntegerField(
        default=0,
        verbose_name="Item-source searches completed so far",
    )
    result_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Price results stored by this run",
    )
    skipped_duplicate_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Parsed results skipped as unchanged duplicates this run",
    )
    error_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Failed searches in this run",
    )


class FetchJob(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        HTTP_ERROR = "http_error", "HTTP error"
        PARSE_ERROR = "parse_error", "Parse error"
        CONFIG_ERROR = "config_error", "Configuration error"
        EMPTY = "empty", "No results"
        OVERSIZED = "oversized", "Response too large"
        BLOCKED = "blocked", "Blocked"
        GIVE_UP = "give_up", "Gave up"

    webupdate = models.ForeignKey(
        WebUpdate,
        on_delete=models.CASCADE,
        related_name="fetch_jobs",
    )
    item = models.ForeignKey(SearchableItem, on_delete=models.CASCADE)
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    search_term = models.CharField(max_length=125)
    search_url = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    # Pattern-matching parsed titles after include/exclude filtering, before dedup.
    # Not raw len(parser.results). Compare stored_count for rows actually inserted.
    result_count = models.PositiveSmallIntegerField(default=0)
    stored_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="SearchResult rows stored for this job after dedup",
    )

    @property
    def skipped_count(self):
        return max(0, self.result_count - self.stored_count)

    class Meta:
        indexes = [
            models.Index(fields=["webupdate", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["webupdate", "item", "source"],
                name="unique_fetchjob_per_webupdate_item_source",
            ),
        ]
        ordering = ["id"]


class SearchResult(models.Model):
    title = models.CharField(
        max_length=250,
        null=False,
        verbose_name="Title returned in search result",
    )

    search_term = models.CharField(
        max_length=125,
        verbose_name="Search term used for this fetch",
    )

    price = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Price returned in search result",
    )

    category = models.CharField(
        max_length=250,
        null=True,
        blank=True,
        verbose_name="Category returned in search result",
    )

    product_line = models.CharField(
        max_length=250,
        null=True,
        blank=True,
        verbose_name="Product line returned in search result",
    )

    item = models.ForeignKey(
        SearchableItem,
        on_delete=models.CASCADE,
        blank=False,
        null=False,
        verbose_name="Item associated with this search result",
        related_query_name="results",
    )

    instock = models.SmallIntegerField(
        default=1,
        blank=True,
        verbose_name="In-stock status returned in search result",
    )

    update = models.ForeignKey(
        WebUpdate,
        on_delete=models.CASCADE,
        related_query_name="results",
        verbose_name="Set of search results from a single web update",
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_query_name="results",
        verbose_name="Search source associated with this search result",
    )


    @classmethod
    def update_from_web(cls, items=None):
        from .scrape import run_web_update

        return run_web_update(items=items)


class ObservedCategoryValue(models.Model):
    """Every raw category/product-line string a parser has ever seen per source.

    Populated unconditionally by ``JSONSearchParser.add_result`` for every row
    processed, independent of whether that row is ultimately accepted or
    rejected by item-level filtering — see the ``item-category-relevance``
    capability. Powers the value-discovery suggestions on the ``SearchableItem``
    form; not a taxonomy and not sourced from ``SearchResult`` (which only ever
    holds accepted rows).
    """

    class FieldName(models.TextChoices):
        CATEGORY = "category", "Category"
        PRODUCT_LINE = "product_line", "Product line"

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        verbose_name="Source this value was observed from",
    )
    field_name = models.CharField(
        max_length=20,
        choices=FieldName.choices,
        verbose_name="Which signal this value was observed for",
    )
    value = models.CharField(
        max_length=250,
        verbose_name="Raw value as observed from the vendor response",
    )
    last_seen = models.DateTimeField(
        verbose_name="When this value was most recently observed",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "field_name", "value"],
                name="unique_observed_category_value",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "field_name"]),
        ]


def observed_values_for_item(item, field_name):
    """Distinct raw values observed for ``field_name``, scoped to ``item``'s sources.

    Ordered most-recently-observed first. Sourced from ``ObservedCategoryValue``,
    not ``SearchResult`` (see that model's docstring) — non-binding suggestions
    for the value-discovery UI, not an enforced taxonomy. Returns ``[]`` when the
    item has no configured sources or none of them have observations yet.
    """
    return list(
        ObservedCategoryValue.objects.filter(
            source__in=Source.objects.filter(itemsource__item=item),
            field_name=field_name,
        )
        .order_by("-last_seen")
        .values_list("source__key", "value")
    )


def vendor_scoped_suggestions_for_items(item_ids, field_name):
    """Vendor-grouped ``ObservedCategoryValue`` suggestions across a selection.

    For every vendor (``Source``) configured via any of ``item_ids``'s
    ``ItemSource``s, returns one group with that vendor's item-count within
    the selection and its distinct observed ``field_name`` values (most
    recently observed first). Unlike ``observed_values_for_item`` (scoped to
    one item's sources with no count), there is no minimum-shared-item
    threshold — a vendor used by a single selected item still gets its own
    group, per the bulk-item-editing design.

    Batched to a bounded number of queries regardless of selection size: one
    query for vendor/count, one for suggestion values — not looped per item.
    """
    item_ids = list(item_ids)
    if not item_ids:
        return []

    vendor_counts = list(
        ItemSource.objects.filter(item_id__in=item_ids)
        .values("source_id")
        .annotate(item_count=Count("item_id", distinct=True))
        .order_by("source_id")
    )
    if not vendor_counts:
        return []

    source_ids = [row["source_id"] for row in vendor_counts]
    counts_by_source = {row["source_id"]: row["item_count"] for row in vendor_counts}
    sources_by_id = {s.pk: s for s in Source.objects.filter(pk__in=source_ids)}

    values_by_source = defaultdict(list)
    for source_id, value in (
        ObservedCategoryValue.objects.filter(
            source_id__in=source_ids, field_name=field_name
        )
        .order_by("source_id", "-last_seen")
        .values_list("source_id", "value")
    ):
        values_by_source[source_id].append(value)

    groups = []
    for source_id in source_ids:
        source = sources_by_id.get(source_id)
        if source is None:
            continue
        groups.append({
            "source": source,
            "item_count": counts_by_source[source_id],
            "values": values_by_source.get(source_id, []),
        })
    groups.sort(key=lambda group: group["source"].key)
    return groups


def tag_activity_for_items(item_ids):
    """Map ``Tag`` pk -> how many of ``item_ids`` currently have that tag.

    One batched ``Count``-over-selection query, not looped per tag — powers
    the "Active on N items in current selection" annotation on each tag's
    tri-state row (design.md Decision 9). Tags with a zero count within the
    selection are simply absent from the returned mapping.
    """
    item_ids = list(item_ids)
    if not item_ids:
        return {}
    return dict(
        Tag.objects.filter(items__id__in=item_ids)
        .annotate(
            item_count=Count("items", filter=Q(items__id__in=item_ids), distinct=True)
        )
        .values_list("pk", "item_count")
    )


def expected_value_activity_for_items(item_ids, field_name):
    """Map ``(value, source)`` -> how many of ``item_ids`` currently store it.

    Fetches each selected item's stored ``expected_<field_name>`` list once
    (one query, bounded by selection size, not one per row) and tallies in
    Python — a ``JSONField`` list of dicts isn't portably SQL-aggregable
    across this app's supported backends. The same pass powers both the
    per-row "Active on N items in current selection" annotation and the
    "Manual entry" group's membership/counts, via any ``source: None`` keys
    in the result (design.md Decision 9).
    """
    item_ids = list(item_ids)
    counts = Counter()
    if not item_ids:
        return counts
    field = f"expected_{field_name}"
    for entries in SearchableItem.objects.filter(pk__in=item_ids).values_list(
        field, flat=True
    ):
        seen = set()
        for entry in entries:
            pair = (entry["value"], entry["source"])
            if pair in seen:
                continue
            seen.add(pair)
            counts[pair] += 1
    return counts


class UpdateSchedule(models.Model):
    """A recurring background scrape defined by a preset cadence.

    A schedule fires ``tracking.tasks.dispatch_fan_out`` on a recurring cadence
    chosen from a small set of presets (see ``Frequency``) rather than a single
    fixed daily run.
    The Huey periodic dispatcher (``tracking/tasks.py::dispatch_scheduled_updates``)
    wakes every minute, finds due schedules, enqueues a run for each, and stamps
    ``last_run_at``.

    ``anchor_time`` is a *local* (``settings.TIME_ZONE``, America/Halifax)
    time-of-day used to place runs; ``last_run_at`` is stored in UTC like every
    other datetime. What ``anchor_time`` means depends on ``frequency``:

    * ``DAILY`` — one run per day, at ``anchor_time``.
    * ``TWICE_DAILY`` — two runs per day, at ``anchor_time`` and ``anchor_time`` + 12h.
    * ``HOURLY`` — one run per hour; only the *minute* of ``anchor_time`` matters
      (that many minutes past each hour); the hour component is ignored.

    Due-checking is interval based (see ``FREQUENCY_INTERVAL_MINUTES``). A schedule
    is due when it is enabled, its interval has elapsed since ``last_run_at`` (or it
    has never run), and local time has reached the most recent aligned occurrence
    that it has not already run for. This keeps sub-daily cadences working and
    guarantees a schedule never double-fires within one period. A window missed
    while the worker was down simply runs once on the next wake (no backfill).
    """

    class Frequency(models.TextChoices):
        HOURLY = "hourly", "Hourly"
        TWICE_DAILY = "twice_daily", "Twice Daily"
        DAILY = "daily", "Daily"

    # Minutes between runs for each preset. Kept next to ``Frequency`` so adding a
    # new preset (e.g. "Every 15 minutes", "Weekly") is one choice above plus one
    # entry here — no schema change.
    FREQUENCY_INTERVAL_MINUTES = {
        Frequency.HOURLY: 60,
        Frequency.TWICE_DAILY: 720,
        Frequency.DAILY: 1440,
    }

    name = models.CharField(
        max_length=100,
        verbose_name="Human-readable name for this schedule",
    )
    frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.DAILY,
        verbose_name="How often this scrape runs",
    )
    anchor_time = models.TimeField(
        verbose_name="Reference time-of-day (America/Halifax) used to place runs",
    )
    enabled = models.BooleanField(
        default=True,
        verbose_name="Whether this schedule is active",
    )
    tag = models.ForeignKey(
        Tag,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="schedules",
        verbose_name="Limit runs to active items with this tag (blank = all active items)",
    )
    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="When this schedule last dispatched a run (UTC)",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    @property
    def interval_minutes(self):
        """Minutes between runs for this schedule's frequency."""
        return self.FREQUENCY_INTERVAL_MINUTES[self.Frequency(self.frequency)]

    def _last_occurrence(self, local_now):
        """Most recent scheduled local datetime at or before ``local_now``.

        ``local_now`` must be an aware datetime already in the local timezone.
        Returns an aware local datetime aligned to ``anchor_time`` per frequency.
        """
        anchor = self.anchor_time
        freq = self.Frequency(self.frequency)

        if freq == self.Frequency.HOURLY:
            candidate = local_now.replace(
                minute=anchor.minute, second=0, microsecond=0
            )
            if candidate > local_now:
                candidate -= timedelta(hours=1)
            return candidate

        base = local_now.replace(
            hour=anchor.hour, minute=anchor.minute, second=0, microsecond=0
        )
        if freq == self.Frequency.TWICE_DAILY:
            # Occurrences fall at ``anchor`` and ``anchor`` + 12h each day; the
            # candidate 12h before ``base`` is the previous day's second slot.
            candidates = [
                base - timedelta(hours=12),
                base,
                base + timedelta(hours=12),
            ]
        else:  # DAILY
            candidates = [base - timedelta(days=1), base]

        past = [c for c in candidates if c <= local_now]
        return max(past) if past else None

    def is_due(self, now):
        """Whether this schedule should fire at aware datetime ``now``.

        Due when: enabled; its per-frequency interval has elapsed since the last
        run (never double-fires within a period); and local time has reached the
        most recent aligned occurrence it hasn't already run for.
        """
        if not self.enabled:
            return False

        interval = timedelta(minutes=self.interval_minutes)
        if self.last_run_at is not None and (now - self.last_run_at) < interval:
            return False

        local_now = timezone.localtime(now)
        occurrence = self._last_occurrence(local_now)
        if occurrence is None:
            return False

        if self.last_run_at is not None and self.last_run_at >= occurrence:
            return False

        return True
