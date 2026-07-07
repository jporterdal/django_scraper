from django.contrib import messages
from django.http import HttpResponse
from django.views.generic import ListView, TemplateView
from django.views.generic.edit import CreateView, DeleteView, UpdateView, View
from django.urls import reverse
from django.db.models import Count, Min, OuterRef, Subquery, F
from django.shortcuts import redirect
from django.utils import timezone
from .models import ItemSource, SearchableItem, SearchResult, Source, Tag, WebUpdate
from .parsers import CCSearchParser
import json


# Create your views here.

def index(request):
    return redirect("view_terms")


def poll(request):
    try:
        source = Source.objects.get(key="cc")
    except Source.DoesNotExist:
        return HttpResponse("No Canada Computers source configured.", status=500)

    parser = CCSearchParser(term="rtx 5070")
    search_url = source.build_search_url("rtx 5070")
    from .fetcher import Fetcher
    from .scrape import _run_parser_search

    _run_parser_search(parser, Fetcher.from_settings(), search_url)
    lp_output = "\t".join(parser.lowest_price()) + "\n"

    with open("found_prices.txt", "w") as f:
        f.writelines(lp_output)
    return HttpResponse("Success!")


class SearchableCreateView(CreateView):
    model = SearchableItem
    fields = ["text"]
    template_name = "tracking/searchableitem_form.html"

    def get_success_url(self):
        return reverse("view_terms")


class SearchableUpdateView(UpdateView):
    model = SearchableItem
    fields = ["text", "priority", "active", "tags"]
    template_name = "tracking/searchableitem_form.html"

    def get_success_url(self):
        return reverse("view_terms")


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
        context["active_tag_id"] = self.request.GET.get("tag", "")

        return context

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("tags")
        tag_id = self.request.GET.get("tag")
        if tag_id:
            queryset = queryset.filter(tags__id=tag_id).distinct()
        latest_update_id = (
            WebUpdate.objects.order_by('-timestamp').values_list('pk', flat=True).first()
        )
        if latest_update_id is None:
            return queryset

        cheapest = SearchResult.objects.filter(
            item=OuterRef("id"),
            update_id=latest_update_id,
            instock=1,
        ).order_by("price")

        return queryset.annotate(
            latest_minprice=Subquery(cheapest.values("price")[:1]),
            latest_minprice_title=Subquery(cheapest.values("title")[:1]),
            latest_minprice_timestamp=Subquery(cheapest.values("update__timestamp")[:1]),
        )


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


class UpdateFromWebView(View):
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
        else:
            messages.error(request, "Invalid update request.")
            return redirect("view_terms")

        search_count = ItemSource.objects.filter(item__active=True)
        if items is not None:
            search_count = search_count.filter(item__in=items)
        item_count = search_count.values("item").distinct().count()

        if item_count == 0:
            messages.warning(
                request,
                "No items with configured sources to update. "
                "Assign sources to active items first.",
            )
            return redirect("view_terms")

        stats = SearchResult.update_from_web(items=items)

        if stats.result_count:
            message = (
                f"Stored {stats.result_count} price result(s) from "
                f"{item_count} item(s)."
            )
            if stats.error_count:
                message += f" {stats.error_count} search(es) failed — see server logs."
            messages.success(request, message)
        elif stats.error_count:
            messages.error(
                request,
                f"All {stats.error_count} search(es) failed — see server logs.",
            )
        else:
            messages.warning(
                request,
                f"Update ran for {item_count} item(s) but no price data was returned.",
            )

        return redirect("view_terms")

class UpdateScheduleCreateView(TemplateView):
    """Placeholder until UpdateSchedule model is implemented."""
    template_name = "tracking/updateschedule_form.html"


class UpdateScheduleListView(ListView):
    """Show scrape-run history; empty after initial install until first /update/."""
    model = WebUpdate
    ordering = ["-timestamp"]
    template_name = "tracking/webupdate_list.html"
