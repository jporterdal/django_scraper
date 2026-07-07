from django.contrib import messages
from django.http import HttpResponse
from django.views.generic import ListView, TemplateView
from django.views.generic.edit import CreateView, UpdateView, View
from django.urls import reverse
from django.db.models import Min, OuterRef, Subquery, F
from django.shortcuts import redirect
from .models import ItemSource, SearchableItem, SearchResult, WebUpdate
from .parsers import CCSearchParser
import json


# Create your views here.

def index(request):
    return redirect("view_terms")


def poll(request):
    parser = CCSearchParser("rtx 5070")
    search_result = parser.search()
    lp_output = ("\t".join(search_result.lowest_price()) + "\n")

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
    fields = ["text", "priority", "active"]
    template_name = "tracking/searchableitem_form.html"

    def get_success_url(self):
        return reverse("view_terms")


class SearchableListView(ListView):
    # Template searchableitem_list.html
    model = SearchableItem

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        sr = SearchResult.objects.values("update", "item").annotate(
            lowest_price=Min("price"),
            timestamp=F('update__timestamp'),
        ).order_by('-timestamp')

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
            forjson[item['id']]['price_history'].append({
                'price': item['lowest_price'],
                'date': timestamp.strftime("%d/%m/%y") if timestamp else "",
            })

        context['items_json'] = json.dumps(list(forjson.values()))

        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        latest_update_id = (
            WebUpdate.objects.order_by('-timestamp').values_list('pk', flat=True).first()
        )
        if latest_update_id is None:
            return queryset

        subq = SearchResult.objects.filter(
            item=OuterRef('id'),
            update_id=latest_update_id,
            price=Min('price'),
        )

        return queryset.annotate(
            latest_minprice=Subquery(subq.values("price")[:1]),
            latest_minprice_title=Subquery(subq.values("title")[:1]),
            latest_minprice_timestamp=Subquery(subq.values("update__timestamp")[:1]),
        )


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

        result_count = SearchResult.update_from_web(items=items)

        if result_count:
            messages.success(
                request,
                f"Stored {result_count} price result(s) from {item_count} item(s).",
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
