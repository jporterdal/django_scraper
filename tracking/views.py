from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView, View
from django.urls import reverse
from django.db.models import Count, Min, OuterRef, Subquery, F
from django.shortcuts import redirect, render
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .forms import ItemSourceForm, SearchableItemCreateForm, SearchableItemForm, SourceForm, UpdateScheduleForm
from .matching import result_matches_item_source
from .models import (
    FetchJob,
    ItemSource,
    SearchableItem,
    SearchResult,
    Source,
    Tag,
    UpdateSchedule,
    WebUpdate,
)
from .parsers import sources as parser_registry
from .tasks import run_web_update_task
import csv
import json
from collections import defaultdict
from datetime import timedelta
from django.conf import settings



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


def _build_source_chart_series(item, results, fetch_jobs):
    """Per-source chart points: solid for stored rows, hollow for unchanged fetches."""
    stored_by_source_update = defaultdict(dict)
    for result in results:
        if not result.instock:
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

        points_by_update = {}
        carry_price = None
        for job in sorted(source_jobs, key=lambda j: j.webupdate.timestamp):
            update_id = job.webupdate_id
            stored = stored_by_source_update.get(source_id, {}).get(update_id)
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

        for update_id, stored in stored_by_source_update.get(source_id, {}).items():
            if update_id not in points_by_update:
                points_by_update[update_id] = stored

        if not source_jobs and source_id not in stored_by_source_update:
            continue

        points = sorted(points_by_update.values(), key=lambda p: p["timestamp"])

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Add New Search Term"
        context["submit_label"] = "Add"
        return context


class SearchableUpdateView(UpdateView):
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

        sr = (
            SearchResult.objects.filter(instock=1)
            .values("update", "item")
            .annotate(
                lowest_price=Min("price"),
                timestamp=F("update__timestamp"),
            )
            .order_by("-timestamp")
        )

        # A bit over-engineered but avoids quadratic/worse costs in case we end up with a lot of item IDs
        item_ids = set([r['item'] for r in sr])  # O(n)
        forjson = {i: {'id': i, 'price_history': []} for i in item_ids}  # O(n), accessible in O(lg n) by re-using key i
        srlist = [{**r, 'id': r['item']} for r in sr]  # O(n) data copy, using new key 'id' for clarity

        while True:  # O(n lg n)
            try:
                item = srlist.pop()
            except IndexError:
                break
            timestamp = item['timestamp']
            if timestamp:
                local_ts = timezone.localtime(timestamp)
                date_str = local_ts.strftime("%d/%m/%y")
            else:
                date_str = ""
            forjson[item['id']]['price_history'].append({
                'price': item['lowest_price'],
                'date': date_str,
            })

        context['items_json'] = json.dumps(list(forjson.values()))
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

        _annotate_item_list_status(context["object_list"])
        context["item_list_stale_threshold_hours"] = ITEM_LIST_STALE_THRESHOLD_HOURS
        return context

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("tags")
        tag_id = self.request.GET.get("tag")
        if tag_id:
            queryset = queryset.filter(tags__id=tag_id).distinct()

        latest_storing_update = Subquery(
            SearchResult.objects.filter(item=OuterRef("pk"), instock=1)
            .order_by("-update__timestamp")
            .values("update_id")[:1]
        )
        cheapest = SearchResult.objects.filter(
            item=OuterRef("pk"),
            update_id=latest_storing_update,
            instock=1,
        ).order_by("price")
        last_checked = Subquery(
            FetchJob.objects.filter(
                item=OuterRef("pk"),
                status=FetchJob.Status.SUCCESS,
            )
            .order_by("-webupdate__timestamp")
            .values("webupdate__timestamp")[:1]
        )

        return queryset.annotate(
            latest_known_minprice=Subquery(cheapest.values("price")[:1]),
            latest_known_minprice_title=Subquery(cheapest.values("title")[:1]),
            last_checked_at=last_checked,
        )


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

        fetch_jobs = list(
            FetchJob.objects.filter(item=item)
            .select_related("source", "webupdate")
            .order_by("webupdate__timestamp", "id")
        )

        chart_data, chart_sources, source_fetch_notes = _build_source_chart_series(
            item, results, fetch_jobs
        )

        for r in results:
            isrc = item_source_by_key.get(r.source_id)
            r.matches = result_matches_item_source(r.title, isrc) if isrc else True

        context["results"] = results
        context["chart_data_json"] = json.dumps(chart_data)
        context["chart_sources"] = chart_sources
        context["source_fetch_notes"] = source_fetch_notes
        context["item_sources"] = item_sources
        context["tags"] = item.tags.all()
        return context


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

    def get_success_url(self):
        messages.success(self.request, f"Tag “{self.object.name}” created.")
        return reverse("view_tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Add Tag"
        context["submit_label"] = "Add Tag"
        return context

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["name"].widget.attrs.update({"class": "form-control"})
        form.fields["color"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "#3498db",
        })
        return form


class TagUpdateView(UpdateView):
    model = Tag
    fields = ["name", "color"]
    template_name = "tracking/tag_form.html"

    def get_success_url(self):
        messages.success(self.request, f"Tag “{self.object.name}” updated.")
        return reverse("view_tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit Tag"
        context["submit_label"] = "Save Changes"
        return context

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["name"].widget.attrs.update({"class": "form-control"})
        form.fields["color"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "#3498db",
        })
        return form


class TagDeleteView(DeleteView):
    model = Tag
    template_name = "tracking/tag_confirm_delete.html"
    context_object_name = "tag"

    def get_success_url(self):
        messages.success(self.request, f"Tag “{self.object.name}” deleted.")
        return reverse("view_tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_count"] = self.object.items.count()
        return context


class ItemSourceUpdateView(UpdateView):
    # Phase 2 Step 5, Task 4 — minimal edit route so include/exclude patterns are
    # editable without the Django admin. Step 7 will add the full ItemSource
    # management UI (list/add/delete); it should reuse this shared ItemSourceForm.
    model = ItemSource
    form_class = ItemSourceForm
    template_name = "tracking/item_source_form.html"

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


class ItemSourceCreateView(CreateView):
    model = ItemSource
    form_class = ItemSourceForm
    template_name = "tracking/item_source_form.html"

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


class ItemSourceDeleteView(DeleteView):
    model = ItemSource
    template_name = "tracking/item_source_confirm_delete.html"
    context_object_name = "item_source"

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
        total_searches = search_qs.count()

        if item_count == 0:
            messages.warning(
                request,
                "No items with configured sources to update. "
                "Assign sources to active items first.",
            )
            return redirect("view_terms")

        # Create the WebUpdate up front so the progress UI has something to poll,
        # then hand the run off to the background worker instead of blocking the
        # request. Under immediate mode (dev/test) the task runs inline.
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.PENDING,
            total_searches=total_searches,
        )

        item_ids = None
        if items is not None:
            item_ids = list(items.values_list("pk", flat=True))

        run_web_update_task(webupdate.pk, item_ids=item_ids)

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
            "timestamp": timezone.localtime(ts).isoformat() if ts else "",
        })
    return rows


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
    for row in _item_export_rows(item):
        writer.writerow(row)
    return response


def export_item_json(request, pk):
    item = get_object_or_404(SearchableItem, pk=pk)
    response = JsonResponse(_item_export_rows(item), safe=False)
    response["Content-Disposition"] = (
        f'attachment; filename="{_export_filename(item, "json")}"'
    )
    return response
