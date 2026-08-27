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
        # Blank text disables the search-term relevance check (see
        # retroactive-result-matching) — this suite is about price/source
        # resolution mechanics, not term matching, and its titles (below)
        # are arbitrary ("A old", "B new", ...) rather than term-derived.
        cls.item = make_item(text="", active=True)
        cls.item_source_a = make_item_source(cls.item, cls.src_a)
        cls.item_source_b = make_item_source(cls.item, cls.src_b)

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

    def _annotated_item(self, object_list):
        return next(item for item in object_list if item.pk == self.item.pk)

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

    def test_latest_price_keeps_stale_cheaper_source(self):
        """A source not re-checked in the latest run still wins if it's cheapest.

        Regression test: min-price comparison must use each source's own most
        recent in-stock price, not be scoped to whichever single WebUpdate
        happened to store a row most recently (dedup skips storing unchanged
        prices, so a stale-but-still-current source was previously dropped
        from the comparison entirely).
        """
        older = self._stamp(make_web_update(), timezone.now() - timedelta(days=1))
        make_search_result(self.item, self.src_a, older, title="A", price=9.99)
        make_search_result(self.item, self.src_b, older, title="B", price=5.25)

        # Only src_a is re-checked/stored on the newer update; src_b's price
        # is unchanged so no new row is stored for it here.
        newer = self._stamp(make_web_update(), timezone.now())
        make_search_result(self.item, self.src_a, newer, title="A", price=7.99)

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 5.25)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_b.key)

    def test_latest_price_picks_cheapest_of_same_source_tied_rows(self):
        """A source's single latest WebUpdate can store multiple in-stock
        variant rows (e.g. regular/foil printings) tied on timestamp; the
        cheapest of those tied rows must win, not an arbitrary one.

        Rows are inserted most-expensive-first to prove the fix doesn't
        accidentally rely on insertion order.
        """
        update = self._stamp(make_web_update(), timezone.now())
        make_search_result(
            self.item, self.src_a, update, title="A foil", price=1.75
        )
        make_search_result(
            self.item, self.src_a, update, title="A regular", price=1.25
        )

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 1.25)
        self.assertEqual(annotated.latest_known_minprice_title, "A regular")
        self.assertEqual(annotated.latest_known_minprice_source, self.src_a.key)

    def test_latest_price_tied_on_price_picks_alphabetical_title(self):
        """When same-source tied rows also tie on price, the alphabetically
        first title wins deterministically, regardless of insertion order.
        """
        update = self._stamp(make_web_update(), timezone.now())
        make_search_result(
            self.item, self.src_a, update, title="Zeta variant", price=3.00
        )
        make_search_result(
            self.item, self.src_a, update, title="Alpha variant", price=3.00
        )

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 3.00)
        self.assertEqual(annotated.latest_known_minprice_title, "Alpha variant")

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

    def test_exclude_pattern_added_after_storage_drops_source_contribution(self):
        """retroactive-result-matching: an exclude pattern added after a row was
        stored removes that source's contribution to Latest price, even though
        the row was accepted (and is cheaper) at fetch time."""
        update = make_web_update()
        make_search_result(self.item, self.src_a, update, title="Widget Foil", price=5.0)
        make_search_result(self.item, self.src_b, update, title="Widget", price=9.0)

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 5.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_a.key)

        self.item_source_a.title_exclude_patterns = ["Foil"]
        self.item_source_a.save(update_fields=["title_exclude_patterns"])

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 9.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_b.key)

    def test_source_with_fully_excluded_window_drops_out_of_comparison(self):
        """A source whose entire recent window is excluded contributes no price
        at all — the cross-source minimum falls back to the other source(s)."""
        self.item_source_a.title_exclude_patterns = ["Foil"]
        self.item_source_a.save(update_fields=["title_exclude_patterns"])

        update = make_web_update()
        make_search_result(self.item, self.src_a, update, title="Widget Foil", price=1.0)
        make_search_result(self.item, self.src_b, update, title="Widget", price=9.0)

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 9.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_b.key)

    def test_older_still_matching_row_used_when_newest_excluded(self):
        """When a source's newest row no longer matches, an older still-matching
        row for the same source is used instead of dropping the source entirely."""
        older = self._stamp(make_web_update(), timezone.now() - timedelta(days=1))
        make_search_result(self.item, self.src_a, older, title="Widget", price=8.0)

        newer = self._stamp(make_web_update(), timezone.now())
        make_search_result(self.item, self.src_a, newer, title="Widget Foil", price=1.0)
        make_search_result(self.item, self.src_b, newer, title="Widget B", price=50.0)

        self.item_source_a.title_exclude_patterns = ["Foil"]
        self.item_source_a.save(update_fields=["title_exclude_patterns"])

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 8.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_a.key)

    def test_loosened_pattern_restores_previously_excluded_row(self):
        """Removing an exclude pattern makes a previously-excluded row eligible
        again — no data was lost while it was excluded."""
        self.item_source_a.title_exclude_patterns = ["Foil"]
        self.item_source_a.save(update_fields=["title_exclude_patterns"])

        update = make_web_update()
        make_search_result(self.item, self.src_a, update, title="Widget Foil", price=1.0)
        make_search_result(self.item, self.src_b, update, title="Widget B", price=9.0)

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 9.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_b.key)

        self.item_source_a.title_exclude_patterns = []
        self.item_source_a.save(update_fields=["title_exclude_patterns"])

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 1.0)
        self.assertEqual(annotated.latest_known_minprice_source, self.src_a.key)

    def test_dedup_timestamp_masking_regression(self):
        """A source's two independently-deduped title threads must be resolved
        separately — a sibling thread's fresh timestamp must not shadow a
        cheaper, still-current thread that dedup left untouched (design.md
        Decision 3 / the dedup-timestamp-masking bug found while implementing
        task 4)."""
        older = self._stamp(make_web_update(), timezone.now() - timedelta(days=5))
        make_search_result(
            self.item, self.src_a, older, title="A widget", price=5.0
        )

        # Thread B is a distinct title, re-stored later at a higher price —
        # its fresh timestamp must not outrank thread A's untouched, cheaper row.
        newer = self._stamp(make_web_update(), timezone.now())
        make_search_result(
            self.item, self.src_a, newer, title="A gadget", price=20.0
        )

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 5.0)
        self.assertEqual(annotated.latest_known_minprice_title, "A widget")
        self.assertEqual(annotated.latest_known_minprice_source, self.src_a.key)

    def test_non_matching_thread_does_not_block_sibling_thread_same_source(self):
        """A thread with no currently-matching row must not block a sibling
        thread on the same source from contributing (item-list-latest-price
        spec scenario "A thread with no currently-matching row does not block
        other threads on the same source")."""
        self.item_source_a.title_exclude_patterns = ["Foil"]
        self.item_source_a.save(update_fields=["title_exclude_patterns"])

        update = make_web_update()
        make_search_result(
            self.item, self.src_a, update, title="Widget Foil", price=1.0
        )
        make_search_result(
            self.item, self.src_a, update, title="Widget", price=9.0
        )

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"])
        self.assertEqual(annotated.latest_known_minprice, 9.0)
        self.assertEqual(annotated.latest_known_minprice_title, "Widget")
        self.assertEqual(annotated.latest_known_minprice_source, self.src_a.key)


class RetroactiveTermAndExpectedValueTests(AuthedClientTestCase):
    """retroactive-result-matching: search-term and expected-value edits
    after storage also retroactively exclude already-stored rows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.src = make_source(key="cc", name="Source A", parser_key="cc")

    def _list_context(self):
        request = RequestFactory().get(reverse("view_terms"))
        view = SearchableListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def _annotated_item(self, object_list, item):
        return next(i for i in object_list if i.pk == item.pk)

    def test_term_edit_excludes_already_stored_row(self):
        item = make_item(text="Lightning Bolt", active=True)
        make_item_source(item, self.src)
        update = make_web_update()
        make_search_result(item, self.src, update, title="Lightning Bolt (NM)", price=1.5)

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"], item)
        self.assertEqual(annotated.latest_known_minprice, 1.5)

        item.text = "Giant Growth"
        item.save(update_fields=["text"])

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"], item)
        self.assertIsNone(annotated.latest_known_minprice)

    def test_expected_product_line_edit_excludes_already_stored_row(self):
        item = make_item(text="Lightning Bolt", active=True)
        make_item_source(item, self.src)
        update = make_web_update()
        make_search_result(
            item, self.src, update,
            title="Lightning Bolt (NM)", price=1.5, product_line="Disney Lorcana",
        )

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"], item)
        self.assertEqual(annotated.latest_known_minprice, 1.5)

        item.expected_product_line = [{"value": "Magic", "source": None}]
        item.save(update_fields=["expected_product_line"])

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"], item)
        self.assertIsNone(annotated.latest_known_minprice)

    def test_expected_category_edit_excludes_already_stored_row(self):
        item = make_item(text="Lightning Bolt", active=True)
        make_item_source(item, self.src)
        update = make_web_update()
        make_search_result(
            item, self.src, update,
            title="Lightning Bolt (NM)", price=1.5, category="Modern Masters 2015",
        )

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"], item)
        self.assertEqual(annotated.latest_known_minprice, 1.5)

        item.expected_category = [{"value": "Strixhaven", "source": None}]
        item.save(update_fields=["expected_category"])

        context = self._list_context()
        annotated = self._annotated_item(context["object_list"], item)
        self.assertIsNone(annotated.latest_known_minprice)
