"""Out-of-stock Latest price + chart / Latest price drift (bugdata.csv sequence)."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import RequestFactory
from django.urls import reverse

from tracking.models import FetchJob, SearchResult, WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_item,
    make_item_source,
    make_search_result,
    make_source,
)
from tracking.views import SearchableItemDetailView, SearchableListView

UTC = ZoneInfo("UTC")

FOREST = "Through the Forest Gate"
HOB = f"{FOREST} (HOB-137) - The Hobbit (Near Mint)"
HOB_FOIL = f"{FOREST} (HOB-137) - The Hobbit Foil (Near Mint)"
HOB_EXT = f"{FOREST} (Extended Art) (HOB-310) - The Hobbit (Near Mint)"
WT_HOB = f"{FOREST} (HOB)"
WT_FOIL = f"{FOREST} (HOB) - Foil"
WT_EXT = f"{FOREST} (310) (Extended Art) (HOB) - Foil"


class _DriftBase(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.hfx = make_source(
            key="hfx", name="HFX", parser_key="cc",
            base_search_url="https://hfx.example/s?q={term}",
        )
        cls.wt = make_source(
            key="wt", name="WT", parser_key="cc",
            base_search_url="https://wt.example/s?q={term}",
        )
        # Blank text disables the search-term relevance check; these tests are
        # about thread-state mechanics, not term matching.
        cls.item = make_item(text="", active=True)
        cls.isrc_hfx = make_item_source(cls.item, cls.hfx)
        cls.isrc_wt = make_item_source(cls.item, cls.wt)

    def _update(self, month, day, hour=12):
        update = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        WebUpdate.objects.filter(pk=update.pk).update(
            timestamp=datetime(2026, month, day, hour, 0, tzinfo=UTC)
        )
        update.refresh_from_db()
        return update

    def _row(self, source, update, title, price, instock=1):
        return make_search_result(
            self.item, source, update, title=title, price=price, instock=instock
        )

    def _job(self, source, update, result_count, stored_count, **extra):
        return FetchJob.objects.create(
            webupdate=update,
            item=self.item,
            source=source,
            search_term=self.item.text,
            status=extra.pop("status", FetchJob.Status.SUCCESS),
            result_count=result_count,
            stored_count=stored_count,
            **extra,
        )

    def _detail_series(self):
        view = SearchableItemDetailView()
        view.object = self.item
        context = view.get_context_data()
        return json.loads(context["chart_data_json"])

    def _list_context(self):
        request = RequestFactory().get(reverse("view_terms"))
        view = SearchableListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def _list_item(self):
        context = self._list_context()
        return next(i for i in context["object_list"] if i.pk == self.item.pk)

    def _sparkline(self):
        history = next(
            i for i in json.loads(self._list_context()["items_json"])
            if i["id"] == self.item.pk
        )["price_history"]
        return [entry["price"] for entry in history]


class BugdataSequenceTests(_DriftBase):
    """hfx 08-19 → 08-23 and wt 08-22 in stock → 08-23 out of stock."""

    def setUp(self):
        super().setUp()
        u19 = self._update(8, 19)
        u21 = self._update(8, 21)
        u22 = self._update(8, 22)
        u23 = self._update(8, 23)
        self.u24 = self._update(8, 24)

        self._row(self.hfx, u19, HOB, 1.25)
        self._row(self.hfx, u19, HOB_FOIL, 1.25)
        self._job(self.hfx, u19, 2, 2)

        self._row(self.hfx, u21, HOB_EXT, 3.00)
        self._job(self.hfx, u21, 3, 1)

        self._row(self.hfx, u22, HOB, 1.50)
        self._row(self.hfx, u22, HOB_FOIL, 2.50)
        self._row(self.hfx, u22, HOB_EXT, 3.50)
        self._job(self.hfx, u22, 3, 3)

        self._row(self.wt, u22, WT_HOB, 1.25)
        self._row(self.wt, u22, WT_FOIL, 2.00)
        self._row(self.wt, u22, WT_EXT, 5.25)
        self._job(self.wt, u22, 3, 3)

        self._row(self.hfx, u23, HOB, 1.75)
        self._row(self.hfx, u23, HOB_FOIL, 2.25)
        self._row(self.hfx, u23, HOB_EXT, 3.25)
        self._job(self.hfx, u23, 3, 3)

        self._row(self.wt, u23, WT_HOB, None, instock=0)
        self._row(self.wt, u23, WT_FOIL, None, instock=0)
        self._row(self.wt, u23, WT_EXT, None, instock=0)
        self._job(self.wt, u23, 3, 3)

    def test_latest_price_ignores_stale_out_of_stock_source(self):
        item = self._list_item()
        self.assertEqual(item.latest_known_minprice, 1.75)
        self.assertEqual(item.latest_known_minprice_source, self.hfx.pk)

    def test_wt_chart_gaps_after_going_out_of_stock(self):
        series = self._detail_series()["wt"]
        self.assertEqual(series["prices"], [1.25, None])

    def test_wt_chart_stays_gap_on_unchanged_later_fetch(self):
        self._job(self.wt, self.u24, 3, 0)
        series = self._detail_series()["wt"]
        self.assertEqual(series["prices"], [1.25, None, None])
        self.assertNotIn("hollow", series["point_styles"][1:])

    def test_hfx_chart_has_no_sibling_spike(self):
        series = self._detail_series()["hfx"]
        self.assertEqual(series["prices"], [1.25, 1.25, 1.50, 1.75])

    def test_hfx_last_chart_point_equals_latest_price(self):
        series = self._detail_series()["hfx"]
        priced = [p for p in series["prices"] if p is not None]
        self.assertEqual(priced[-1], self._list_item().latest_known_minprice)

    def test_sparkline_last_point_equals_latest_price(self):
        spark = self._sparkline()
        self.assertEqual(spark, [1.25, 1.25, 1.50, 1.75])
        self.assertEqual(spark[-1], self._list_item().latest_known_minprice)


class ThreadStateTests(_DriftBase):
    def test_out_of_stock_thread_does_not_block_in_stock_sibling(self):
        u1 = self._update(9, 1)
        u2 = self._update(9, 2)
        self._row(self.hfx, u1, "A", 1.00)
        self._row(self.hfx, u1, "B", 2.00)
        self._job(self.hfx, u1, 2, 2)
        self._row(self.hfx, u2, "A", None, instock=0)
        self._job(self.hfx, u2, 2, 1)
        self.assertEqual(self._list_item().latest_known_minprice, 2.00)
        self.assertEqual(self._detail_series()["hfx"]["prices"], [1.00, 2.00])

    def test_back_in_stock_resumes_line(self):
        u1, u2, u3 = self._update(9, 1), self._update(9, 2), self._update(9, 3)
        self._row(self.hfx, u1, "A", 1.25)
        self._job(self.hfx, u1, 1, 1)
        self._row(self.hfx, u2, "A", None, instock=0)
        self._job(self.hfx, u2, 1, 1)
        self._row(self.hfx, u3, "A", 1.50)
        self._job(self.hfx, u3, 1, 1)
        self.assertEqual(self._list_item().latest_known_minprice, 1.50)
        series = self._detail_series()["hfx"]
        self.assertEqual(series["prices"], [1.25, None, 1.50])
        self.assertEqual(series["point_styles"][2], "solid")

    def test_absent_title_keeps_last_state(self):
        # Documented limitation (design.md D5): a title the vendor stops
        # returning at all keeps its last stored state.
        u1, u2 = self._update(9, 1), self._update(9, 2)
        self._row(self.hfx, u1, "A", 3.00)
        self._job(self.hfx, u1, 1, 1)
        self._job(self.hfx, u2, 1, 0)
        self.assertEqual(self._list_item().latest_known_minprice, 3.00)
        self.assertEqual(self._detail_series()["hfx"]["prices"], [3.00, 3.00])

    def test_same_update_in_stock_row_beats_out_of_stock_row(self):
        u1 = self._update(9, 1)
        self._row(self.hfx, u1, "A", None, instock=0)
        self._row(self.hfx, u1, "A", 4.00)
        self._job(self.hfx, u1, 2, 2)
        self.assertEqual(self._list_item().latest_known_minprice, 4.00)

    def test_costlier_sibling_only_update_is_hollow(self):
        u1, u2 = self._update(9, 1), self._update(9, 2)
        self._row(self.hfx, u1, "A", 1.25)
        self._row(self.hfx, u1, "B", 3.00)
        self._job(self.hfx, u1, 2, 2)
        self._row(self.hfx, u2, "B", 2.00)
        self._job(self.hfx, u2, 2, 1)
        series = self._detail_series()["hfx"]
        self.assertEqual(series["prices"], [1.25, 1.25])
        self.assertEqual(series["point_styles"], ["solid", "hollow"])

    def test_lowest_price_change_is_solid(self):
        u1, u2 = self._update(9, 1), self._update(9, 2)
        self._row(self.hfx, u1, "A", 1.25)
        self._job(self.hfx, u1, 1, 1)
        self._row(self.hfx, u2, "A", 1.00)
        self._job(self.hfx, u2, 1, 1)
        series = self._detail_series()["hfx"]
        self.assertEqual(series["point_styles"], ["solid", "solid"])

    def test_sparkline_ignores_rows_excluded_by_current_criteria(self):
        self.isrc_hfx.title_exclude_patterns = ["Foil"]
        self.isrc_hfx.save()
        u1, u2 = self._update(9, 1), self._update(9, 2)
        self._row(self.hfx, u1, "Widget", 2.00)
        self._job(self.hfx, u1, 1, 1)
        self._row(self.hfx, u2, "Widget Foil", 0.50)
        self._job(self.hfx, u2, 1, 1)
        item = self._list_item()
        self.assertEqual(item.latest_known_minprice, 2.00)
        spark = self._sparkline()
        self.assertEqual(spark, [2.00, 2.00])
        self.assertEqual(spark[-1], item.latest_known_minprice)
