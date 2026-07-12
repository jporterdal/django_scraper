from django.test import TestCase
from django.urls import reverse
import json

from tracking.models import SearchResult, WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_cc_source, make_item, make_web_update
from tracking.views import SearchableListView


class ItemDetailViewTests(AuthedClientTestCase):
    """Phase 2 Step 6 — item detail / history page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = make_cc_source()
        cls.item = make_item(text="rtx 5070", active=True)
        cls.update = make_web_update()

    def _result(self, title, price, instock=1, update=None):
        return SearchResult.objects.create(
            title=title,
            search_term=self.item.text,
            price=price,
            category="Hardware",
            item=self.item,
            instock=instock,
            source=self.source,
            update=update or self.update,
        )

    def test_item_detail_200(self):
        self._result("MSI RTX 5070", 799.99)
        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)

    def test_item_detail_lists_all_results(self):
        self._result("MSI RTX 5070 In Stock", 799.99, instock=1)
        self._result("ASUS RTX 5070 Out", 899.99, instock=0)
        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        self.assertContains(response, "MSI RTX 5070 In Stock")
        self.assertContains(response, "ASUS RTX 5070 Out")

    def test_item_detail_chart_context(self):
        self._result("MSI RTX 5070", 799.99, instock=1)
        self._result("MSI RTX 5070 Cheaper", 749.99, instock=1)
        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        chart_data = json.loads(response.context["chart_data_json"])
        self.assertIn(self.source.key, chart_data)
        series = chart_data[self.source.key]
        self.assertIn("labels", series)
        self.assertIn("prices", series)
        # Lowest in-stock price for the update is used.
        self.assertEqual(series["prices"], [749.99])

    def test_list_page_history_link(self):
        response = self.client.get(reverse("view_terms"))
        self.assertContains(
            response, reverse("item_detail", args=[self.item.pk])
        )
