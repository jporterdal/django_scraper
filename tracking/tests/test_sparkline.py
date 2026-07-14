"""Step 3 — list sparkline source-scoped price history + carry-forward."""

import json
from datetime import timedelta

from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from tracking.models import FetchJob, WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_item,
    make_item_source,
    make_search_result,
    make_source,
    make_web_update,
)
from tracking.views import SearchableListView


class SparklineSourceScopedTests(AuthedClientTestCase):
    """List price_history follows the Latest Price source (3b) with carry-forward (3c)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.src_a = make_source(key="cc", name="Source A", parser_key="cc")
        cls.src_b = make_source(
            key="amz",
            name="Source B",
            parser_key="shopify",
            base_search_url="https://example.com/s?q={term}",
        )
        cls.item = make_item(text="spark widget", active=True)
        make_item_source(cls.item, cls.src_a)
        make_item_source(cls.item, cls.src_b)

    def _stamp(self, update, when):
        WebUpdate.objects.filter(pk=update.pk).update(timestamp=when)
        update.refresh_from_db()
        return update

    def _list_context(self):
        request = RequestFactory().get(reverse("view_terms"))
        view = SearchableListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def _item_json(self, context):
        items = json.loads(context["items_json"])
        return next(i for i in items if i["id"] == self.item.pk)

    def _annotated_item(self, queryset):
        return queryset.get(pk=self.item.pk)

    def _seed_two_update_multi_source(self):
        """Older/newer updates: cc [100, 90], amz [200, 70]. Latest Price → amz $70."""
        base = timezone.now() - timedelta(days=2)
        older = self._stamp(make_web_update(), base)
        newer = self._stamp(make_web_update(), base + timedelta(days=1))

        make_search_result(
            self.item, self.src_a, older, title="A old", price=100.0
        )
        make_search_result(
            self.item, self.src_b, older, title="B old", price=200.0
        )
        make_search_result(
            self.item, self.src_a, newer, title="A new", price=90.0
        )
        make_search_result(
            self.item, self.src_b, newer, title="B new", price=70.0
        )
        return older, newer

    def test_price_history_uses_latest_price_source(self):
        self._seed_two_update_multi_source()
        context = self._list_context()
        item_data = self._item_json(context)
        prices = [p["price"] for p in item_data["price_history"]]
        self.assertEqual(prices, [200.0, 70.0])

    def test_sparkline_latest_point_matches_latest_price(self):
        self._seed_two_update_multi_source()
        context = self._list_context()
        item_data = self._item_json(context)
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 70.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_b.key)
        self.assertEqual(item_data["price_history"][-1]["price"], 70.0)
        self.assertEqual(
            item_data["price_history"][-1]["price"],
            annotated.latest_known_minprice,
        )

    def test_price_history_empty_when_no_latest_price(self):
        context = self._list_context()
        item_data = self._item_json(context)
        annotated = self._annotated_item(context["object_list"])
        self.assertIsNone(annotated.latest_known_minprice)
        self.assertIsNone(annotated.latest_known_minprice_source)
        self.assertEqual(item_data["price_history"], [])

    def test_price_history_carry_forward_on_unchanged_dedup(self):
        base = timezone.now() - timedelta(days=3)
        stored_update = self._stamp(make_web_update(), base)
        carry_update = self._stamp(make_web_update(), base + timedelta(days=1))
        no_match_update = self._stamp(make_web_update(), base + timedelta(days=2))

        make_search_result(
            self.item, self.src_b, stored_update, title="B stored", price=50.0
        )
        # Also store on src_a so multi-source noise exists; Latest Price still src_b.
        make_search_result(
            self.item, self.src_a, stored_update, title="A stored", price=80.0
        )

        FetchJob.objects.create(
            webupdate=stored_update,
            item=self.item,
            source=self.src_b,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=1,
            stored_count=1,
        )
        FetchJob.objects.create(
            webupdate=carry_update,
            item=self.item,
            source=self.src_b,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=2,
            stored_count=0,
        )
        FetchJob.objects.create(
            webupdate=no_match_update,
            item=self.item,
            source=self.src_b,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=0,
            stored_count=0,
        )

        context = self._list_context()
        item_data = self._item_json(context)
        prices = [p["price"] for p in item_data["price_history"]]
        self.assertEqual(prices, [50.0, 50.0])
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 50.0)
        self.assertEqual(item_data["price_history"][-1]["price"], 50.0)

    def test_view_terms_embeds_source_scoped_history(self):
        self._seed_two_update_multi_source()
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"priceChart-{self.item.pk}")
        items = json.loads(response.context["items_json"])
        item_data = next(i for i in items if i["id"] == self.item.pk)
        self.assertEqual(
            [p["price"] for p in item_data["price_history"]],
            [200.0, 70.0],
        )
