from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView, View
from django.urls import reverse
from django.db.models import Count, F, OuterRef, Q, Subquery, Window
from django.db.models.functions import RowNumber
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404
from urllib.parse import urlparse
import logging
from .forms import (
    BulkAddItemsForm,
    BulkEditItemsForm,
    ItemSourceForm,
    ItemSourceFormSet,
    SearchableItemCreateForm,
    SearchableItemForm,
    SourceForm,
    UpdateScheduleForm,
    apply_bulk_edit,
    create_items_from_bulk_add,
)
from .demo import is_demo
from .demo import policy as demo_policy
from .demo.protection import PROTECTED_MESSAGE, is_protected
from .matching import result_matches_item_source
from .metadata import get_item_metadata, request_metadata_refresh
from .metadata_providers import get_metadata_providers
from .models import (
    FetchJob,
    ItemMetadata,
    ItemSource,
    SearchableItem,
    SearchResult,
    Source,
    Tag,
    UpdateSchedule,
    WebUpdate,
)
from .parsers import sources as parser_registry
from .scheduling_status import schedules_may_not_fire
from .tasks import dispatch_fan_out
import csv
import json
from collections import defaultdict
from datetime import timedelta
from django.conf import settings

logger = logging.getLogger(__name__)


def _safe_next_url(request, candidate=None):
    """Return a safe same-origin/relative redirect target, or None."""
    if candidate is None:
        candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return None
    if url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return None



class DemoProtectedObjectMixin:
    """Demo mode: refuse any request (view or write) aimed at protected seed data.

    ``get_protected_target`` returns the seed-checkable object; refused
    requests redirect with a message and never reach the form or delete logic.
    """

    def get_protected_target(self):
        return self.get_object()

    def get_protected_redirect_url(self, target):
        return reverse("view_terms")

    def dispatch(self, request, *args, **kwargs):
        if is_demo():
            target = self.get_protected_target()
            if is_protected(target):
                messages.warning(request, PROTECTED_MESSAGE)
                return redirect(self.get_protected_redirect_url(target))
        return super().dispatch(request, *args, **kwargs)


def _refuse_protected_item(request, item):
    """Redirect response for a metadata action on a protected item, else None."""
    if is_protected(item):
        messages.warning(request, PROTECTED_MESSAGE)
        return redirect("item_detail", pk=item.pk)
    return None


# Create your views here.

ITEM_LIST_STALE_THRESHOLD_HOURS = 48

ITEM_LIST_STATUS = {
    "never_checked": ("Never checked", "text-bg-light"),
    "failed": ("Failed", "text-bg-danger"),
    "no_matches": ("No matches", "text-bg-warning"),
    "updated": ("Updated", "text-bg-success"),
    "unchanged": ("Unchanged", "text-bg-secondary"),
}


def _rollup_item_list_status(jobs):
    """Mutually exclusive status from FetchJobs in one run for one item."""
    if not jobs:
        return "never_checked"
    if any(job.status != FetchJob.Status.SUCCESS for job in jobs):
        return "failed"
    parsed_total = sum(job.result_count for job in jobs)
    stored_total = sum(job.stored_count for job in jobs)
    if parsed_total == 0:
        return "no_matches"
    if stored_total > 0:
        return "updated"
    return "unchanged"


def _annotate_item_list_status(items):
    """Attach list_status, stale flag, and per-source failure detail to each item."""
    if not items:
        return
    item_ids = [item.pk for item in items]
    ever_success = set(
        FetchJob.objects.filter(
            item_id__in=item_ids,
            status=FetchJob.Status.SUCCESS,
        ).values_list("item_id", flat=True)
    )
    items_with_sources = set(
        ItemSource.objects.filter(item_id__in=item_ids)
        .values_list("item_id", flat=True)
        .distinct()
    )
    stale_cutoff = timezone.now() - timedelta(hours=ITEM_LIST_STALE_THRESHOLD_HOURS)

    latest_webupdate_by_item = {}
    for job in (
        FetchJob.objects.filter(item_id__in=item_ids)
        .order_by("item_id", "-webupdate__timestamp", "-id")
        .values("item_id", "webupdate_id")
    ):
        if job["item_id"] not in latest_webupdate_by_item:
            latest_webupdate_by_item[job["item_id"]] = job["webupdate_id"]

    webupdate_ids = set(latest_webupdate_by_item.values())
    jobs_by_item = defaultdict(list)
    if webupdate_ids:
        for job in FetchJob.objects.filter(
            item_id__in=item_ids,
            webupdate_id__in=webupdate_ids,
        ).select_related("source"):
            if latest_webupdate_by_item.get(job.item_id) == job.webupdate_id:
                jobs_by_item[job.item_id].append(job)

    for item in items:
        if item.pk not in ever_success:
            status = "never_checked"
        else:
            status = _rollup_item_list_status(jobs_by_item.get(item.pk, []))
        label, badge_class = ITEM_LIST_STATUS[status]
        item.list_status = status
        item.list_status_label = label
        item.list_status_badge_class = badge_class

        last_checked_at = getattr(item, "last_checked_at", None)
        item.list_is_stale = (
            item.active
            and item.pk in items_with_sources
            and (last_checked_at is None or last_checked_at < stale_cutoff)
        )

        failed_labels = []
        if status == "failed":
            for job in jobs_by_item.get(item.pk, []):
                if job.status != FetchJob.Status.SUCCESS:
                    failed_labels.append(job.source.name or job.source.key)
        item.list_failed_source_labels = sorted(set(failed_labels))
        item.list_failed_source_detail = ", ".join(item.list_failed_source_labels)


def _format_fetch_job_note(job):
    """Muted chart heading note for one item+source FetchJob."""
    ts = timezone.localtime(job.webupdate.timestamp)
    time_str = ts.strftime("%H:%M %b %d")
    if job.status != FetchJob.Status.SUCCESS:
        return f"[Last Checked: {time_str}. {job.get_status_display()}]"
    parsed = job.result_count
    stored = job.stored_count
    if parsed == 0:
        trailing = "no matches"
    elif stored > 0:
        trailing = "price changed"
    else:
        trailing = "unchanged"
    return (
        f"[Last Checked: {time_str}. Parsed {parsed}, stored {stored}, {trailing}]"
    )


def _source_price_points(stored_by_update, source_jobs):
    """Chronological price points for one source: stored, carry-forward, orphans.

    ``stored_by_update`` maps update_id → ``{"price", "timestamp", "kind": "stored"}``.
    Returns a list of ``{"price", "timestamp", "kind"}`` oldest→newest. Shared by
    list sparklines and the detail chart so point selection cannot drift.
    """
    points_by_update = {}
    carry_price = None
    for job in sorted(source_jobs, key=lambda j: j.webupdate.timestamp):
        update_id = job.webupdate_id
        stored = stored_by_update.get(update_id)
        if stored is not None:
            carry_price = stored["price"]
            points_by_update[update_id] = {
                "price": stored["price"],
                "timestamp": job.webupdate.timestamp,
                "kind": "stored",
            }
        elif (
            job.status == FetchJob.Status.SUCCESS
            and job.result_count > 0
            and job.stored_count == 0
            and carry_price is not None
        ):
            points_by_update[update_id] = {
                "price": carry_price,
                "timestamp": job.webupdate.timestamp,
                "kind": "unchanged",
            }

    for update_id, stored in stored_by_update.items():
        if update_id not in points_by_update:
            points_by_update[update_id] = stored

    return sorted(points_by_update.values(), key=lambda p: p["timestamp"])


def _resolve_latest_known_prices(items):
    """Per-item Latest price, retroactively re-validated against current criteria.

    A source's results are independently deduplicated per distinct title
    (``scrape.py``'s ``_dedupe_unit_candidates`` keys on ``(item, source,
    title)``), so a single source can carry several concurrently-valid,
    independently-timestamped title "threads" at once. Resolution is
    two-level, in one windowed query (not one query per source or thread —
    see retroactive-result-matching and design.md Decision 3):

    - Per thread ``(item_id, source_id, title)``: walk that thread's in-stock
      rows newest-first and take the first one that still matches the item's/
      item-source's *current* relevance criteria. A thread with no
      currently-matching row contributes nothing — this scoping (rather than
      partitioning by ``(item_id, source_id)`` alone) is what prevents one
      thread's fresh timestamp from shadowing a sibling thread's still-current,
      untouched price.
    - Per source: the minimum, cheapest-then-alphabetical-title, across that
      source's thread winners.

    Returns ``{item_id: (price, title, source_id)}`` for items with at least
    one contributing source; cross-source ties resolve cheapest-then-source_id,
    matching this view's previous SQL ordering.
    """
    item_ids = [item.pk for item in items]
    if not item_ids:
        return {}

    ranked_results = (
        SearchResult.objects.filter(item_id__in=item_ids, instock=1)
        .annotate(
            _thread_rank=Window(
                expression=RowNumber(),
                partition_by=[F("item_id"), F("source_id"), F("title")],
                order_by=["-update__timestamp", "price", "title"],
            )
        )
        .order_by("item_id", "source_id", "title", "_thread_rank")
    )

    rows_by_thread = defaultdict(list)
    for row in ranked_results:
        rows_by_thread[(row.item_id, row.source_id, row.title)].append(row)

    threads_by_pair = defaultdict(list)
    for item_id, source_id, title in rows_by_thread:
        threads_by_pair[(item_id, source_id)].append(title)

    item_sources = ItemSource.objects.filter(item_id__in=item_ids).select_related(
        "item", "source"
    )

    winners_by_item = defaultdict(list)
    for item_source in item_sources:
        pair = (item_source.item_id, item_source.source_id)
        thread_winners = []
        for title in threads_by_pair.get(pair, ()):
            rows = rows_by_thread[(item_source.item_id, item_source.source_id, title)]
            winner = next(
                (
                    row
                    for row in rows
                    if result_matches_item_source(
                        row.title, row.category, row.product_line, item_source
                    )
                ),
                None,
            )
            if winner is None:
                logger.info(
                    "Retroactive match: item=%s source=%s title=%r exhausted thread "
                    "(%d rows) with no currently-matching result",
                    item_source.item_id, item_source.source_id, title, len(rows),
                )
            else:
                thread_winners.append(winner)

        if thread_winners:
            source_winner = min(thread_winners, key=lambda row: (row.price, row.title))
            winners_by_item[item_source.item_id].append(
                (source_winner.price, item_source.source_id, source_winner.title)
            )

    latest_prices = {}
    for item_id, winners in winners_by_item.items():
        price, source_id, title = min(winners, key=lambda winner: (winner[0], winner[1]))
        latest_prices[item_id] = (price, title, source_id)
    return latest_prices


def _build_source_chart_series(item, results, fetch_jobs):
    """Per-source chart points: solid for stored rows, hollow for unchanged fetches.

    Skips any row whose ``matches`` flag is False — a row that no longer
    satisfies the item's/item-source's *current* relevance criteria (see
    retroactive-result-matching) still appears in the raw results table, but
    is excluded from the price-history chart it feeds.
    """
    stored_by_source_update = defaultdict(dict)
    for result in results:
        if not result.matches:
            continue
        if not result.instock or result.price is None:
            continue
        existing = stored_by_source_update[result.source_id].get(result.update_id)
        if existing is None or result.price < existing["price"]:
            stored_by_source_update[result.source_id][result.update_id] = {
                "price": result.price,
                "timestamp": result.update.timestamp,
                "kind": "stored",
            }

    jobs_by_source = defaultdict(list)
    for job in fetch_jobs:
        jobs_by_source[job.source_id].append(job)

    chart_data = {}
    chart_sources = []
    source_fetch_notes = {}

    for source_id in sorted(
        set(stored_by_source_update) | set(jobs_by_source),
        key=lambda sid: (
            jobs_by_source[sid][0].source.key
            if sid in jobs_by_source
            else str(sid)
        ),
    ):
        source_jobs = jobs_by_source.get(source_id, [])
        source_key = None
        source_name = None
        if source_jobs:
            source_key = source_jobs[0].source.key
            source_name = source_jobs[0].source.name
            latest_job = max(
                source_jobs,
                key=lambda job: (job.webupdate.timestamp, job.pk),
            )
            source_fetch_notes[source_key] = _format_fetch_job_note(latest_job)
        else:
            for result in results:
                if result.source_id == source_id:
                    source_key = result.source.key
                    source_name = result.source.name
                    break
            if source_key is None:
                continue

        if not source_jobs and source_id not in stored_by_source_update:
            continue

        points = _source_price_points(
            stored_by_source_update.get(source_id, {}),
            source_jobs,
        )

        labels = []
        prices = []
        point_styles = []
        tooltips = []
        for point in points:
            ts = point["timestamp"]
            labels.append(
                timezone.localtime(ts).strftime("%d/%m/%y") if ts else ""
            )
            prices.append(point["price"])
            date_label = timezone.localtime(ts).strftime("%b %d")
            if point["kind"] == "stored":
                point_styles.append("solid")
                tooltips.append(f"{date_label} — ${point['price']:.2f} (price changed)")
            else:
                point_styles.append("hollow")
                tooltips.append(
                    f"{date_label} — ${point['price']:.2f} (confirmed, unchanged)"
                )

        chart_data[source_key] = {
            "labels": labels,
            "prices": prices,
            "point_styles": point_styles,
            "tooltips": tooltips,
        }
        chart_sources.append({
            "key": source_key,
            "name": source_name,
            "fetch_note": source_fetch_notes.get(source_key, ""),
        })

    return chart_data, chart_sources, source_fetch_notes

def index(request):
    return redirect("view_terms")


class SearchableCreateView(CreateView):
    model = SearchableItem
    form_class = SearchableItemCreateForm
    template_name = "tracking/searchableitem_form.html"

    def get_success_url(self):
        return reverse("view_terms")

    def form_valid(self, form):
        if is_demo() and demo_policy.visitor_item_limit_reached():
            messages.error(
                self.request,
                f"The demo item limit ({settings.DEMO_MAX_VISITOR_ITEMS}) has been reached. "
                "The sandbox resets after an hour of inactivity.",
            )
            return redirect("view_terms")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Add New Search Term"
        context["submit_label"] = "Add"
        return context


class BulkAddItemsView(View):
    """Create many SearchableItems (and optional ItemSources) in one submit."""

    template_name = "tracking/bulk_add_form.html"

    def get(self, request):
        initial = {}
        tag_pk = request.GET.get("tag")
        if tag_pk and Tag.objects.filter(pk=tag_pk).exists():
            initial["tag"] = str(tag_pk)
        return self._render(request, BulkAddItemsForm(initial=initial), ItemSourceFormSet())

    def post(self, request):
        form = BulkAddItemsForm(request.POST)
        formset = ItemSourceFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            if is_demo() and demo_policy.visitor_item_limit_reached(
                adding=len(form.cleaned_data["search_terms"])
            ):
                messages.error(
                    request,
                    f"Adding these would exceed the demo item limit "
                    f"({settings.DEMO_MAX_VISITOR_ITEMS}); nothing was added.",
                )
                return self._render(request, form, formset)
            items = create_items_from_bulk_add(
                terms=form.cleaned_data["search_terms"],
                tag=form.cleaned_data["tag"],
                priority=form.cleaned_data["priority"],
                source_forms=formset.forms,
                metadata_provider_key=form.cleaned_data["metadata_provider_key"],
            )
            count = len(items)
            messages.success(
                request,
                f"Added {count} item{'s' if count != 1 else ''}.",
            )
            return redirect("view_terms")
        return self._render(request, form, formset)

    def _render(self, request, form, formset):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "formset": formset,
                "form_title": "Bulk Add Items",
                "submit_label": "Create Items",
            },
        )


class BulkEditItemsView(View):
    """Persistent multi-round bulk-edit workspace for an existing item selection.

    Entered via POST from ``view_terms`` carrying checked ``item_ids``. Every
    subsequent Apply/remove/Done action on the workspace re-POSTs here with
    the working ``item_ids`` carried forward as hidden fields (design.md
    Decision 2) plus an ``in_workspace`` marker distinguishing those
    resubmits from the initial entry — the initial entry has no bulk-edit
    form fields in its POST body, so it must not be run through
    ``apply_bulk_edit``.
    """

    template_name = "tracking/bulk_edit_workspace.html"

    def get(self, request):
        return redirect("view_terms")

    def post(self, request):
        item_ids = self._clean_item_ids(request.POST.getlist("item_ids"))

        if "in_workspace" not in request.POST:
            # Initial entry from view_terms's selection checkboxes.
            if not item_ids:
                messages.warning(request, "No items selected.")
                return redirect("view_terms")
            return self._render(request, item_ids)

        if "done" in request.POST:
            return redirect("view_terms")

        remove_id = request.POST.get("remove_item_id")
        if remove_id:
            try:
                remove_id = int(remove_id)
            except ValueError:
                remove_id = None
            if remove_id is not None:
                item_ids = [pk for pk in item_ids if pk != remove_id]
            return self._render(request, item_ids)

        form = BulkEditItemsForm(request.POST, item_ids=item_ids)
        results = None
        if form.is_valid():
            results = apply_bulk_edit(item_ids, form.cleaned_data)
            form = None  # Reset to all-fields-leave-unchanged for the next round.
        return self._render(request, item_ids, form=form, results=results)

    @staticmethod
    def _clean_item_ids(raw_ids):
        item_ids = []
        seen = set()
        for raw in raw_ids:
            try:
                pk = int(raw)
            except (TypeError, ValueError):
                continue
            if pk not in seen:
                seen.add(pk)
                item_ids.append(pk)
        return item_ids

    def _render(self, request, item_ids, form=None, results=None):
        items_by_pk = {
            item.pk: item
            for item in SearchableItem.objects.filter(pk__in=item_ids).prefetch_related("tags")
        }
        # Drop any id that no longer resolves to an existing item (task 2.5) —
        # normal missing-row handling, not concurrency detection.
        items = [items_by_pk[pk] for pk in item_ids if pk in items_by_pk]
        valid_ids = [item.pk for item in items]

        if not valid_ids:
            messages.warning(request, "No items remain in the bulk-edit selection.")
            return redirect("view_terms")

        if form is None:
            form = BulkEditItemsForm(item_ids=valid_ids)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "items": items,
                "item_ids": valid_ids,
                "results": results,
            },
        )


class SearchableUpdateView(DemoProtectedObjectMixin, UpdateView):
    model = SearchableItem
    form_class = SearchableItemForm
    template_name = "tracking/searchableitem_form.html"

    def get_success_url(self):
        return reverse("view_terms")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit Search Term"
        context["submit_label"] = "Save Changes"
        return context


class SearchableListView(ListView):
    # Template searchableitem_list.html
    model = SearchableItem

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        object_list = list(context["object_list"])

        latest_prices = _resolve_latest_known_prices(object_list)
        for item in object_list:
            price, title, source_id = latest_prices.get(item.pk, (None, None, None))
            item.latest_known_minprice = price
            item.latest_known_minprice_title = title
            item.latest_known_minprice_source = source_id

        item_source_pairs = []
        for item in object_list:
            source_id = getattr(item, "latest_known_minprice_source", None)
            if source_id is not None:
                item_source_pairs.append((item.pk, source_id))

        forjson = {
            item.pk: {"id": item.pk, "price_history": []} for item in object_list
        }

        if item_source_pairs:
            pair_filter = Q()
            for item_id, source_id in item_source_pairs:
                pair_filter |= Q(item_id=item_id, source_id=source_id)

            stored_by_item_source_update = defaultdict(dict)
            for result in (
                SearchResult.objects.filter(instock=1, price__isnull=False)
                .filter(pair_filter)
                .select_related("update")
            ):
                bucket = stored_by_item_source_update[
                    (result.item_id, result.source_id)
                ]
                existing = bucket.get(result.update_id)
                if existing is None or result.price < existing["price"]:
                    bucket[result.update_id] = {
                        "price": result.price,
                        "timestamp": result.update.timestamp,
                        "kind": "stored",
                    }

            jobs_by_item_source = defaultdict(list)
            for job in (
                FetchJob.objects.filter(pair_filter)
                .select_related("webupdate")
                .order_by("webupdate__timestamp", "id")
            ):
                jobs_by_item_source[(job.item_id, job.source_id)].append(job)

            for item_id, source_id in item_source_pairs:
                points = _source_price_points(
                    stored_by_item_source_update.get((item_id, source_id), {}),
                    jobs_by_item_source.get((item_id, source_id), []),
                )
                for point in points:
                    ts = point["timestamp"]
                    if ts:
                        date_str = timezone.localtime(ts).strftime("%d/%m/%y")
                    else:
                        date_str = ""
                    forjson[item_id]["price_history"].append(
                        {
                            "price": point["price"],
                            "date": date_str,
                        }
                    )

        for item in object_list:
            item.metadata_thumbnail_url = ""
            item_metadata = get_item_metadata(item)
            if item_metadata is not None and item_metadata.status == ItemMetadata.Status.MATCHED:
                provider_cls = get_metadata_providers().get(item.metadata_provider_key)
                if provider_cls is not None:
                    display = provider_cls().to_display(item_metadata.payload)
                    item.metadata_thumbnail_url = display.get("thumbnail_url", "")

        context["items_json"] = json.dumps(list(forjson.values()))
        context["tags"] = Tag.objects.all()
        active_tag_id = self.request.GET.get("tag", "")
        context["active_tag_id"] = active_tag_id
        if active_tag_id:
            tag = Tag.objects.filter(pk=active_tag_id).first()
            if tag:
                context["active_tag"] = tag
                context["active_tag_item_count"] = SearchableItem.objects.filter(
                    active=True, tags=tag
                ).count()
                context["active_tag_updatable_count"] = (
                    ItemSource.objects.filter(item__active=True, item__tags=tag)
                    .values("item")
                    .distinct()
                    .count()
                )

        context["object_list"] = object_list
        _annotate_item_list_status(context["object_list"])
        context["item_list_stale_threshold_hours"] = ITEM_LIST_STALE_THRESHOLD_HOURS
        return context

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("tags").select_related("metadata")
        tag_id = self.request.GET.get("tag")
        if tag_id:
            queryset = queryset.filter(tags__id=tag_id).distinct()

        last_checked = Subquery(
            FetchJob.objects.filter(
                item=OuterRef("pk"),
                status=FetchJob.Status.SUCCESS,
            )
            .order_by("-webupdate__timestamp")
            .values("webupdate__timestamp")[:1]
        )

        # "Latest price" (latest_known_minprice/_title/_source) is no longer a
        # SQL annotation here — it requires walking each source's recent rows
        # in Python against current relevance criteria (see
        # retroactive-result-matching), which regex-based patterns can't
        # express in a portable SQL subquery. Computed in get_context_data via
        # _resolve_latest_known_prices() and set as plain attributes instead.
        return queryset.annotate(last_checked_at=last_checked)


class SearchableItemDetailView(DetailView):
    # Phase 2 Step 6 — item detail / history page: a table of ALL stored
    # SearchResult rows for the item plus a per-source Chart.js price-history
    # line chart (lowest in-stock price per WebUpdate).
    model = SearchableItem
    template_name = "tracking/searchableitem_detail.html"
    context_object_name = "item"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        item = self.object

        results = list(
            SearchResult.objects.filter(item=item)
            .select_related("source", "update")
            .order_by("-update__timestamp", "source__key", "price")
        )

        item_sources = list(
            ItemSource.objects.filter(item=item).select_related("source")
        )
        item_source_by_key = {isrc.source_id: isrc for isrc in item_sources}

        for r in results:
            isrc = item_source_by_key.get(r.source_id)
            r.matches = (
                result_matches_item_source(r.title, r.category, r.product_line, isrc)
                if isrc else True
            )

        fetch_jobs = list(
            FetchJob.objects.filter(item=item)
            .select_related("source", "webupdate")
            .order_by("webupdate__timestamp", "id")
        )

        chart_data, chart_sources, source_fetch_notes = _build_source_chart_series(
            item, results, fetch_jobs
        )

        context["results"] = results
        context["chart_data_json"] = json.dumps(chart_data)
        context["chart_sources"] = chart_sources
        context["source_fetch_notes"] = source_fetch_notes
        context["item_sources"] = item_sources
        context["tags"] = item.tags.all()

        item_metadata = get_item_metadata(item)
        context["item_metadata"] = item_metadata
        # Same underlying signal as the schedules warning (see
        # scheduling_status.schedules_may_not_fire): the periodic drain task
        # that would move this out of unfetched/pending only runs inside a
        # live `run_huey` consumer process, which this request-serving
        # process has no way to confirm is actually running.
        context["metadata_fetch_may_not_process"] = (
            not is_demo()
            and item_metadata is not None
            and item_metadata.status in (ItemMetadata.Status.UNFETCHED, ItemMetadata.Status.PENDING)
            and schedules_may_not_fire()
        )
        if item_metadata is not None:
            provider_cls = get_metadata_providers().get(item.metadata_provider_key)
            if provider_cls is not None:
                provider = provider_cls()
                if item_metadata.status == ItemMetadata.Status.MATCHED:
                    context["metadata_display"] = provider.to_display(item_metadata.payload)
                elif item_metadata.status == ItemMetadata.Status.NEEDS_REVIEW:
                    candidates = (item_metadata.payload or {}).get("candidates", [])
                    context["metadata_candidates"] = [
                        {
                            "external_id": candidate.get("external_id", ""),
                            **provider.to_display(candidate.get("payload", {})),
                        }
                        for candidate in candidates
                    ]
        return context


class MetadataRetryView(View):
    """Manual "Retry metadata fetch" action (task 3.6) — re-enters the shared entrypoint."""

    def post(self, request, pk):
        item = get_object_or_404(SearchableItem, pk=pk)
        refused = _refuse_protected_item(request, item)
        if refused:
            return refused
        request_metadata_refresh(item)
        messages.success(request, "Metadata refresh requested.")
        return redirect("item_detail", pk=item.pk)


class MetadataSelectCandidateView(View):
    """Operator picks a disambiguation candidate: pin it and materialize the match.

    The candidate's payload was already fetched as part of the ``needs_review``
    resolution, so this sets ``pinned_external_id``/``status=matched`` directly
    from the stored candidate rather than enqueuing another fetch.
    """

    def post(self, request, pk):
        item = get_object_or_404(SearchableItem, pk=pk)
        refused = _refuse_protected_item(request, item)
        if refused:
            return refused
        external_id = request.POST.get("external_id", "")
        item_metadata = get_object_or_404(ItemMetadata, item=item)
        candidates = (item_metadata.payload or {}).get("candidates", [])
        selected = next(
            (c for c in candidates if c.get("external_id") == external_id), None
        )
        if selected is None:
            messages.error(request, "Unknown metadata candidate selected.")
            return redirect("item_detail", pk=item.pk)

        item_metadata.pinned_external_id = external_id
        item_metadata.external_id = external_id
        item_metadata.payload = selected.get("payload", {})
        item_metadata.status = ItemMetadata.Status.MATCHED
        item_metadata.fetched_at = timezone.now()
        item_metadata.save(
            update_fields=["pinned_external_id", "external_id", "payload", "status", "fetched_at"]
        )
        messages.success(request, "Metadata match selected.")
        return redirect("item_detail", pk=item.pk)


class MetadataSetExternalIdView(View):
    """Manual external-ID entry fallback (task 6.3): pin it and enqueue a fetch."""

    def post(self, request, pk):
        item = get_object_or_404(SearchableItem, pk=pk)
        refused = _refuse_protected_item(request, item)
        if refused:
            return refused
        external_id = (request.POST.get("external_id") or "").strip()
        if not external_id:
            messages.error(request, "Enter an external identifier.")
            return redirect("item_detail", pk=item.pk)

        item_metadata, _ = ItemMetadata.objects.get_or_create(item=item)
        item_metadata.pinned_external_id = external_id
        item_metadata.save(update_fields=["pinned_external_id"])
        request_metadata_refresh(item)
        messages.success(request, "Metadata refresh requested for the given identifier.")
        return redirect("item_detail", pk=item.pk)


class TagListView(ListView):
    model = Tag
    template_name = "tracking/tag_list.html"
    context_object_name = "tags"

    def get_queryset(self):
        return Tag.objects.annotate(item_count=Count("items"))


class TagCreateView(CreateView):
    model = Tag
    fields = ["name", "color"]
    template_name = "tracking/tag_form.html"

    def form_valid(self, form):
        if is_demo() and demo_policy.visitor_tag_limit_reached():
            messages.error(
                self.request,
                f"The demo tag limit ({settings.DEMO_MAX_VISITOR_TAGS}) has been reached.",
            )
            return redirect("view_tags")
        return super().form_valid(form)

    def get_success_url(self):
        messages.success(self.request, f"Tag “{self.object.name}” created.")
        next_url = _safe_next_url(self.request)
        if next_url:
            bulk_path = reverse("bulk_add")
            if urlparse(next_url).path.rstrip("/") == bulk_path.rstrip("/"):
                return f"{bulk_path}?tag={self.object.pk}"
            return next_url
        return reverse("view_tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Add Tag"
        context["submit_label"] = "Add Tag"
        next_param = self.request.POST.get("next") or self.request.GET.get("next")
        safe_next = _safe_next_url(self.request, next_param)
        context["next_param"] = next_param if safe_next else ""
        context["back_url"] = safe_next or reverse("view_tags")
        context["back_label"] = "Back to Bulk Add" if (
            safe_next
            and urlparse(safe_next).path.rstrip("/") == reverse("bulk_add").rstrip("/")
        ) else "Back to Tags"
        return context

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["name"].widget.attrs.update({"class": "form-control"})
        form.fields["color"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "#3498db",
        })
        return form


class TagUpdateView(DemoProtectedObjectMixin, UpdateView):
    model = Tag
    fields = ["name", "color"]
    template_name = "tracking/tag_form.html"

    def get_protected_redirect_url(self, target):
        return reverse("view_tags")

    def get_success_url(self):
        messages.success(self.request, f"Tag “{self.object.name}” updated.")
        return reverse("view_tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit Tag"
        context["submit_label"] = "Save Changes"
        context["back_url"] = reverse("view_tags")
        context["back_label"] = "Back to Tags"
        return context

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["name"].widget.attrs.update({"class": "form-control"})
        form.fields["color"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "#3498db",
        })
        return form


class TagDeleteView(DemoProtectedObjectMixin, DeleteView):
    model = Tag
    template_name = "tracking/tag_confirm_delete.html"
    context_object_name = "tag"

    def get_protected_redirect_url(self, target):
        return reverse("view_tags")

    def get_success_url(self):
        messages.success(self.request, f"Tag “{self.object.name}” deleted.")
        return reverse("view_tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_count"] = self.object.items.count()
        return context


class ItemSourceUpdateView(DemoProtectedObjectMixin, UpdateView):
    # Phase 2 Step 5, Task 4 — minimal edit route so include/exclude patterns are
    # editable without the Django admin. Step 7 will add the full ItemSource
    # management UI (list/add/delete); it should reuse this shared ItemSourceForm.
    model = ItemSource
    form_class = ItemSourceForm
    template_name = "tracking/item_source_form.html"

    def get_protected_redirect_url(self, target):
        return reverse("item_sources", args=[target.item_id])

    def get_success_url(self):
        messages.success(self.request, "Item source updated.")
        return reverse("view_terms")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit Item Source"
        context["submit_label"] = "Save Changes"
        return context


class SourceListView(ListView):
    model = Source
    template_name = "tracking/source_list.html"
    context_object_name = "sources"

    def get_queryset(self):
        return Source.objects.annotate(item_count=Count("itemsource")).order_by("key")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["registered_parser_keys"] = list(parser_registry.keys())
        return context


class SourceCreateView(CreateView):
    model = Source
    form_class = SourceForm
    template_name = "tracking/source_form.html"

    def get_success_url(self):
        messages.success(self.request, f"Source “{self.object.key}” created.")
        return reverse("view_sources")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Add Source"
        context["submit_label"] = "Add Source"
        return context


class SourceUpdateView(UpdateView):
    model = Source
    form_class = SourceForm
    template_name = "tracking/source_form.html"

    def get_success_url(self):
        messages.success(self.request, f"Source “{self.object.key}” updated.")
        return reverse("view_sources")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit Source"
        context["submit_label"] = "Save Changes"
        return context


class SourceDeleteView(DeleteView):
    model = Source
    template_name = "tracking/source_confirm_delete.html"
    context_object_name = "source"

    def get_success_url(self):
        messages.success(self.request, f"Source “{self.object.key}” deleted.")
        return reverse("view_sources")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_count"] = ItemSource.objects.filter(source=self.object).count()
        context["result_count"] = SearchResult.objects.filter(source=self.object).count()
        context["fetch_job_count"] = FetchJob.objects.filter(source=self.object).count()
        return context


class ItemSourceListView(ListView):
    template_name = "tracking/item_source_list.html"
    context_object_name = "item_sources"

    def get_queryset(self):
        self.item = get_object_or_404(SearchableItem, pk=self.kwargs["pk"])
        return (
            ItemSource.objects.filter(item=self.item)
            .select_related("source")
            .order_by("source__key")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item"] = self.item
        return context


class ItemSourceCreateView(DemoProtectedObjectMixin, CreateView):
    model = ItemSource
    form_class = ItemSourceForm
    template_name = "tracking/item_source_form.html"

    def get_protected_target(self):
        return get_object_or_404(SearchableItem, pk=self.kwargs["pk"])

    def get_protected_redirect_url(self, target):
        return reverse("item_sources", args=[target.pk])

    def dispatch(self, request, *args, **kwargs):
        self.item = get_object_or_404(SearchableItem, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # On add, only offer sources not already linked to this item.
        linked = ItemSource.objects.filter(item=self.item).values_list(
            "source", flat=True
        )
        form.fields["source"].queryset = Source.objects.exclude(pk__in=linked)
        return form

    def form_valid(self, form):
        form.instance.item = self.item
        return super().form_valid(form)

    def get_success_url(self):
        messages.success(self.request, "Item source added.")
        return reverse("item_sources", args=[self.item.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item"] = self.item
        context["form_title"] = f"Add Source to “{self.item.text}”"
        context["submit_label"] = "Add Source"
        return context


class ItemSourceDeleteView(DemoProtectedObjectMixin, DeleteView):
    model = ItemSource
    template_name = "tracking/item_source_confirm_delete.html"
    context_object_name = "item_source"

    def get_protected_redirect_url(self, target):
        return reverse("item_sources", args=[target.item_id])

    def get_success_url(self):
        messages.success(self.request, "Item source removed.")
        return reverse("item_sources", args=[self.object.item_id])


class UpdateFromWebView(View):
    """Enqueue a background price scrape from the item list.

    ``mode=all`` updates every active item; ``mode=selected`` scopes to
    checked rows; ``mode=tag`` scopes to active items carrying ``tag_id``
    (same item set as a tag-scoped ``UpdateSchedule``, but triggered once
    on demand rather than by the periodic dispatcher).
    """

    def get(self, request):
        return redirect("view_terms")

    def post(self, request):
        mode = request.POST.get("mode")
        items = None

        if mode == "all":
            pass
        elif mode == "selected":
            item_ids = request.POST.getlist("item_ids")
            if not item_ids:
                messages.warning(request, "No items selected.")
                return redirect("view_terms")
            items = SearchableItem.objects.filter(pk__in=item_ids, active=True)
            if not items.exists():
                messages.warning(
                    request,
                    "No active items in selection. Inactive items are skipped.",
                )
                return redirect("view_terms")
        elif mode == "tag":
            tag_id = request.POST.get("tag_id")
            if not tag_id:
                messages.warning(request, "No tag specified.")
                return redirect("view_terms")
            if not Tag.objects.filter(pk=tag_id).exists():
                messages.error(request, "Unknown tag.")
                return redirect("view_terms")
            items = SearchableItem.objects.filter(active=True, tags=tag_id)
            if not items.exists():
                messages.warning(
                    request,
                    "No active items with this tag.",
                )
                return redirect("view_terms")
        else:
            messages.error(request, "Invalid update request.")
            return redirect("view_terms")

        search_qs = ItemSource.objects.filter(item__active=True)
        if items is not None:
            search_qs = search_qs.filter(item__in=items)
        item_count = search_qs.values("item").distinct().count()

        if item_count == 0:
            messages.warning(
                request,
                "No items with configured sources to update. "
                "Assign sources to active items first.",
            )
            return redirect("view_terms")

        if is_demo():
            if demo_policy.search_results_full():
                messages.error(
                    request,
                    "The demo has stored as many results as it allows; updates are "
                    "paused until the sandbox resets after an hour of inactivity.",
                )
                return redirect("view_terms")
            claimed, wait_seconds = demo_policy.claim_update_slot()
            if not claimed:
                messages.warning(
                    request,
                    f"Demo updates run at most once every "
                    f"{settings.DEMO_UPDATE_MIN_INTERVAL_SECONDS} seconds; "
                    f"try again in {wait_seconds} second{'s' if wait_seconds != 1 else ''}.",
                )
                return redirect("view_terms")

        item_ids = None
        if items is not None:
            item_ids = list(items.values_list("pk", flat=True))

        # Fan out one Huey task per ItemSource (D8): dispatch_fan_out creates
        # the WebUpdate (PENDING, total_searches set) so the progress UI has
        # something to poll, then hands each unit off to the background
        # worker instead of blocking the request. Under immediate mode
        # (dev/test) each unit task runs inline.
        webupdate = dispatch_fan_out(item_ids=item_ids)

        messages.info(
            request,
            f"Started a background price update for {item_count} item(s). "
            "Progress is shown below.",
        )
        return redirect(f"{reverse('view_terms')}?update={webupdate.pk}")

class UpdateProgressView(View):
    """Return the HTMX progress partial for a background WebUpdate run.

    The partial self-polls (`hx-get` + `hx-trigger="every 2s"`) while the run is
    PENDING/RUNNING and stops polling once it reaches DONE/FAILED, swapping in a
    final summary.
    """

    def get(self, request, pk):
        webupdate = get_object_or_404(WebUpdate, pk=pk)
        return render(
            request,
            "tracking/_update_progress.html",
            {"update": webupdate},
        )


class WebUpdateListView(ListView):
    """Scrape-run history: paginated WebUpdate rows (name ``view_updates``).

    Summary columns use scalar fields on ``WebUpdate`` only; per-run ``FetchJob``
    rows are lazy-loaded via ``WebUpdateFetchJobsPartialView`` on expand.
    """
    model = WebUpdate
    template_name = "tracking/webupdate_list.html"
    paginate_by = 25  # Fixed page size; no setting/env var.

    def get_queryset(self):
        return WebUpdate.objects.order_by("-timestamp")


class WebUpdateFetchJobsPartialView(View):
    """HTMX partial: nested FetchJob table for one scrape run."""

    def get(self, request, pk):
        webupdate = get_object_or_404(WebUpdate, pk=pk)
        fetch_jobs = list(
            FetchJob.objects.filter(webupdate=webupdate).select_related(
                "item", "source"
            )
        )
        fetch_jobs.sort(
            key=lambda job: (
                0 if job.status != FetchJob.Status.SUCCESS else 1,
                job.item.text.lower(),
            )
        )
        return render(
            request,
            "tracking/_webupdate_fetch_jobs.html",
            {"webupdate": webupdate, "fetch_jobs": fetch_jobs},
        )


class UpdateScheduleListView(ListView):
    model = UpdateSchedule
    template_name = "tracking/updateschedule_list.html"
    context_object_name = "schedules"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_scheduler_warning"] = schedules_may_not_fire()
        return context


class UpdateScheduleCreateView(CreateView):
    model = UpdateSchedule
    form_class = UpdateScheduleForm
    template_name = "tracking/updateschedule_form.html"

    def get_success_url(self):
        messages.success(self.request, f"Schedule “{self.object.name}” created.")
        return reverse("view_schedules")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Add Schedule"
        context["submit_label"] = "Add Schedule"
        return context


class UpdateScheduleUpdateView(UpdateView):
    model = UpdateSchedule
    form_class = UpdateScheduleForm
    template_name = "tracking/updateschedule_form.html"

    def get_success_url(self):
        messages.success(self.request, f"Schedule “{self.object.name}” updated.")
        return reverse("view_schedules")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit Schedule"
        context["submit_label"] = "Save Changes"
        return context


class UpdateScheduleDeleteView(DeleteView):
    model = UpdateSchedule
    template_name = "tracking/updateschedule_confirm_delete.html"
    context_object_name = "schedule"

    def get_success_url(self):
        messages.success(self.request, f"Schedule “{self.object.name}” deleted.")
        return reverse("view_schedules")


# ---------------------------------------------------------------------------
# Phase 3 Step 6 — price-history export (CSV / JSON) per item.
# ---------------------------------------------------------------------------

# Column order shared by the CSV header and the JSON object keys so the two
# formats stay in lockstep.
EXPORT_FIELDNAMES = [
    "source",
    "search_term",
    "title",
    "price",
    "instock",
    "category",
    "product_line",
    "timestamp",
]


def _item_export_rows(item):
    """Yield one ordered dict-like row per ``SearchResult`` for ``item``.

    Rows are ordered deterministically (newest update first, then source key,
    then price) and the FK-heavy columns are loaded via ``select_related`` to
    avoid N+1 queries. Timestamps are localized to ``settings.TIME_ZONE``.
    """
    results = (
        SearchResult.objects.filter(item=item)
        .select_related("source", "update")
        .order_by("-update__timestamp", "source__key", "price")
    )
    rows = []
    for r in results:
        ts = r.update.timestamp
        rows.append({
            "source": r.source.key,
            "search_term": r.search_term,
            "title": r.title,
            "price": r.price,
            "instock": r.instock,
            "category": r.category or "",
            "product_line": r.product_line or "",
            "timestamp": timezone.localtime(ts).isoformat() if ts else "",
        })
    return rows


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value):
    """Neutralize spreadsheet formula injection (demo visitors download each other's data)."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _export_filename(item, extension):
    return f"item-{item.pk}-price-history.{extension}"


def export_item_csv(request, pk):
    item = get_object_or_404(SearchableItem, pk=pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{_export_filename(item, "csv")}"'
    )
    writer = csv.DictWriter(response, fieldnames=EXPORT_FIELDNAMES)
    writer.writeheader()
    demo = is_demo()
    for row in _item_export_rows(item):
        if demo:
            row = {key: _csv_safe(value) for key, value in row.items()}
        writer.writerow(row)
    return response


def export_item_json(request, pk):
    item = get_object_or_404(SearchableItem, pk=pk)
    response = JsonResponse(_item_export_rows(item), safe=False)
    response["Content-Disposition"] = (
        f'attachment; filename="{_export_filename(item, "json")}"'
    )
    return response
