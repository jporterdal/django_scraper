from urllib.parse import quote_plus

from django.db import models


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
        max_length=3,
        primary_key=True,
        verbose_name="String key indicating which Parser should be used when searching with this Source"
    )

    base_search_url = models.CharField(
        max_length=500,
        blank=False,
        verbose_name="Search URL template; use {term} for the URL-encoded query string",
    )

    def build_search_url(self, term, url_suffix=""):
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


class WebUpdate(models.Model):
    class Meta:
        indexes = [
            models.Index(fields=["timestamp"]),
        ]

    timestamp = models.DateTimeField(
        auto_now_add=True,
    )


class FetchJob(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        HTTP_ERROR = "http_error", "HTTP error"
        PARSE_ERROR = "parse_error", "Parse error"
        CONFIG_ERROR = "config_error", "Configuration error"
        EMPTY = "empty", "No results"

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
    result_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["webupdate", "status"]),
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
        null=False,
        blank=False,
        verbose_name="Price returned in search result",
    )

    category = models.CharField(
        max_length=250,
        null=True,
        blank=True,
        verbose_name="Category returned in search result",
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
