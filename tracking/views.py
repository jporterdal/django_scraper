from django.shortcuts import render
from django.http import HttpResponse
from django.views.generic import ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView, View
from django.urls import reverse
from django.db.models import Min, Max, OuterRef, Subquery, F
from django.shortcuts import redirect
from .models import SearchableItem, SearchResult, WebUpdate
from .parsers import CCSearchParser
import json


# Create your views here.

def index(request):
    return HttpResponse("Hello world.")


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
    def get_success_url(self):
        return reverse('view_terms')


class SearchableListView(ListView):
    model = SearchableItem

    def get_context_data(self):
        context = super().get_context_data()

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
            forjson[item['id']]['price_history'].append(
                {'price': item['lowest_price'],
                 'date': item['timestamp'].strftime("%d/%m/%y")
                 }
            )

        context['items_json'] = json.dumps(list(forjson.values()))
        print(context['items_json'])

        return context


    def get_queryset(self):
        latest_update = WebUpdate.objects.filter(timestamp=Max('timestamp'))
        subq = SearchResult.objects.filter(
            item=OuterRef('id'),
            update=latest_update.values("id")[:1],  # the [:1] notation keeps as QuerySet of length 1 rather than resolving to a dict
            price=Min('price')
        )

        return super().get_queryset().annotate(
            latest_minprice=Subquery(subq.values("price")[:1]),
            latest_minprice_title=Subquery(subq.values("title")[:1]),
            latest_minprice_timestamp=Subquery(subq.values("update__timestamp")[:1])
        )


class UpdateFromWebView(View):
    def get(self, request):
        SearchResult.update_from_web()

        return redirect('view_terms')

class UpdateScheduleCreateView(CreateView):
    #model = UpdateSchedule
    def get_success_url(self):
        return reverse('view_updates')


class UpdateScheduleListView(ListView):
    #model = UpdateSchedule
    pass
