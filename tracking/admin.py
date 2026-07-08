from django.contrib import admin

from .models import FetchJob, ItemSource, SearchableItem, SearchResult, Source, WebUpdate


@admin.register(SearchableItem)
class SearchableItemAdmin(admin.ModelAdmin):
    list_display = ["text", "priority", "active"]
    list_filter = ["active", "priority", "tags"]
    filter_horizontal = ["tags"]
    search_fields = ["text"]


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ["key", "parser_key", "name", "base_search_url"]


@admin.register(ItemSource)
class ItemSourceAdmin(admin.ModelAdmin):
    list_display = ["item", "source", "url_suffix"]
    list_filter = ["source"]
    fields = ["item", "source", "url_suffix", "title_include_patterns", "title_exclude_patterns"]


@admin.register(WebUpdate)
class WebUpdateAdmin(admin.ModelAdmin):
    list_display = ["timestamp"]
    ordering = ["-timestamp"]


@admin.register(FetchJob)
class FetchJobAdmin(admin.ModelAdmin):
    list_display = [
        "webupdate",
        "item",
        "source",
        "status",
        "http_status",
        "result_count",
        "duration_ms",
    ]
    list_filter = ["status", "source"]
    readonly_fields = [
        "webupdate",
        "item",
        "source",
        "search_term",
        "search_url",
        "status",
        "http_status",
        "error_message",
        "duration_ms",
        "result_count",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SearchResult)
class SearchResultAdmin(admin.ModelAdmin):
    list_display = ["item", "source", "search_term", "title", "price", "instock", "update"]
    list_filter = ["source", "instock"]
    search_fields = ["search_term", "title"]
