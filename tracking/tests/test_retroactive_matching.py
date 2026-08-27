"""retroactive-result-matching — detail-page chart re-validation (task 6.4).

The price-history chart must exclude a row that no longer matches current
relevance criteria, while the raw results table still lists every stored row
(matching and non-matching) — see item-list-latest-price/spec.md and
retroactive-result-matching/spec.md.
"""

import json

from django.test import RequestFactory
from django.urls import reverse

from tracking.models import WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item, make_item_source, make_search_result, make_source
from tracking.views import SearchableItemDetailView


class DetailChartRetroactiveExclusionTests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = make_source(key="cc", name="Source A", parser_key="cc")
        cls.item = make_item(text="Lightning Bolt", active=True)
        cls.item_source = make_item_source(cls.item, cls.source)

    def _context(self):
        request = RequestFactory().get("/")
        view = SearchableItemDetailView()
        view.request = request
        view.object = self.item
        return view.get_context_data()

    def test_chart_excludes_excluded_row_but_table_still_lists_it(self):
        self.item_source.title_exclude_patterns = ["Foil"]
        self.item_source.save(update_fields=["title_exclude_patterns"])

        update = WebUpdate.objects.create()
        make_search_result(
            self.item, self.source, update, title="Lightning Bolt Foil", price=5.0
        )

        context = self._context()

        chart_data = json.loads(context["chart_data_json"])
        self.assertEqual(chart_data.get(self.source.key, {}).get("prices", []), [])

        results = context["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].matches)

    def test_chart_includes_matching_row_and_table_lists_it_too(self):
        update = WebUpdate.objects.create()
        make_search_result(
            self.item, self.source, update, title="Lightning Bolt (NM)", price=1.5
        )

        context = self._context()

        chart_data = json.loads(context["chart_data_json"])
        self.assertEqual(chart_data[self.source.key]["prices"], [1.5])

        results = context["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].matches)

    def test_response_shows_excluded_row_grayed_out_in_full_table(self):
        self.item_source.title_exclude_patterns = ["Foil"]
        self.item_source.save(update_fields=["title_exclude_patterns"])

        update = WebUpdate.objects.create()
        make_search_result(
            self.item, self.source, update, title="Lightning Bolt Foil", price=5.0
        )

        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lightning Bolt Foil")
