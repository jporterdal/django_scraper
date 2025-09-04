from django.db import models
from django.db.models import F
from . import parsers


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


class WebUpdate(models.Model):
    class Meta:
        indexes = [
            models.Index(fields=["timestamp"]),
        ]

    timestamp = models.DateTimeField(
        auto_now_add=True,
    )


class SearchResult(models.Model):
    title = models.CharField(
        max_length=250,
        null=False,
        verbose_name="Title returned in search result",
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
    def update_from_web(cls):
        active_searches = ItemSource.objects.filter(
            item__active=True,
        )
        kws, webupdate = [], None

        for act in active_searches:
            try:
                parser = parsers.sources[act.source.key](term=act.item.text)
            except:
                # TODO: handle? at least log
                print("update_from_web() encountered an exception!")
                print(f"parser key = {act.source.key},  search term = {act.item.text}")
                continue

            parser.search()

            for r in parser.results:
                # Create only one webupdate entry for all results
                if webupdate is None:
                    webupdate = WebUpdate()
                    webupdate.save()

                kws.append({
                    "title": r["title"],
                    "price": r["price"],
                    "category": r["category"],
                    "item": act.item,
                    "instock": r["instock"],
                    "source": act.source,
                    "update": webupdate,
                })

        cls.objects.bulk_create([cls(**kw) for kw in kws])

        return len(kws)
