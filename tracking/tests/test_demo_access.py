"""demo-mode: no-login access, deny-by-default writes, bounded inputs (tasks 3.1-3.7)."""

from datetime import timedelta

from django.contrib.sessions.models import Session
from django.test import override_settings
from django.urls import URLPattern, reverse
from django.utils import timezone

from tracking import urls as tracking_urls
from tracking.demo.middleware import DEMO_ALLOWED_WRITE_URL_NAMES
from tracking.models import (
    DemoState,
    ItemSource,
    SearchableItem,
    SearchResult,
    Source,
    Tag,
    UpdateSchedule,
    WebUpdate,
)

from .base import AuthedClientTestCase
from .demo_base import SEED_ITEM_PKS, DemoTestCase

DUMMY_KWARGS = {"int": 999999, "string": "zz"}


def _dummy_kwargs(pattern):
    return {
        name: DUMMY_KWARGS[type(conv).__name__.replace("Converter", "").lower()]
        for name, conv in pattern.pattern.converters.items()
    }


class DemoAccessTests(DemoTestCase):
    def test_cookie_less_visitor_sees_item_list_directly(self):
        response = self.client.get("/view_terms/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["user"].username, "demo")
        self.assertFalse(response.context["user"].is_staff)
        self.assertFalse(response.context["user"].is_superuser)

    def test_browsing_creates_no_session_rows(self):
        before = Session.objects.count()
        self.client.get("/view_terms/")
        self.client.get(f"/item/{SEED_ITEM_PKS[0]}/")
        self.client.post("/add_term/", {"text": "Vexing Starfall", "metadata_provider_key": ""})
        self.client.post("/update/", {"mode": "all"})
        self.client.get("/view_terms/")
        self.assertEqual(Session.objects.count(), before)


class DemoWriteAllowlistTests(DemoTestCase):
    def test_allowlist_names_exist(self):
        names = {p.name for p in tracking_urls.urlpatterns if isinstance(p, URLPattern)}
        self.assertLessEqual(DEMO_ALLOWED_WRITE_URL_NAMES, names)

    def test_every_route_is_allowlisted_or_forbidden(self):
        for pattern in tracking_urls.urlpatterns:
            path = reverse(pattern.name, kwargs=_dummy_kwargs(pattern))
            response = self.client.post(path, {})
            if pattern.name in DEMO_ALLOWED_WRITE_URL_NAMES:
                self.assertNotEqual(response.status_code, 403, pattern.name)
            else:
                self.assertEqual(response.status_code, 403, pattern.name)

    def test_source_and_schedule_writes_rejected_and_change_nothing(self):
        source = Source.objects.first()
        schedule = UpdateSchedule.objects.first()
        before = (Source.objects.count(), UpdateSchedule.objects.count())
        posts = [
            ("/sources/add/", {"key": "evil", "name": "Evil", "parser_key": "shopify",
                               "base_search_url": "https://evil.example/?q={term}"}),
            (f"/sources/{source.pk}/edit/", {"name": "Changed",
                                              "base_search_url": "{term}{term:>999999999}"}),
            (f"/sources/{source.pk}/delete/", {}),
            ("/schedules/add/", {"name": "x", "frequency": "hourly", "anchor_time": "01:00"}),
            (f"/schedules/{schedule.pk}/edit/", {"name": "Changed"}),
            (f"/schedules/{schedule.pk}/delete/", {}),
            ("/add_update/", {"name": "x", "frequency": "hourly", "anchor_time": "01:00"}),
        ]
        for path, data in posts:
            self.assertEqual(self.client.post(path, data).status_code, 403, path)
        self.assertEqual((Source.objects.count(), UpdateSchedule.objects.count()), before)
        source.refresh_from_db()
        self.assertNotEqual(source.name, "Changed")

    def test_allowlisted_write_succeeds(self):
        response = self.client.post("/add_term/", {"text": "Hollowbrook Seer"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SearchableItem.objects.filter(text="Hollowbrook Seer").exists())

    def test_oversized_request_rejected(self):
        before = SearchableItem.objects.count()
        response = self.client.post("/add_term/", {"text": "x", "junk": "y" * (70 * 1024)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SearchableItem.objects.count(), before)


class DemoFramingTemplateTests(DemoTestCase):
    def test_banner_shows_last_reset_age(self):
        DemoState.objects.filter(pk=1).update(last_reset_at=timezone.now() - timedelta(minutes=10))
        response = self.client.get("/view_terms/")
        self.assertContains(response, 'id="demo-banner"')
        self.assertContains(response, "last reset 10\xa0minutes ago")

    def test_admin_logout_and_source_schedule_controls_hidden(self):
        page = self.client.get("/view_terms/")
        self.assertNotContains(page, 'href="/admin/"')
        self.assertNotContains(page, "Log out")
        sources = self.client.get("/sources/")
        self.assertNotContains(sources, "/sources/add/")
        self.assertNotContains(sources, "/edit/")
        schedules = self.client.get("/schedules/")
        self.assertNotContains(schedules, "/schedules/add/")
        self.assertNotContains(schedules, "/edit/")
        self.assertContains(schedules, "shown for illustration")


class NormalModeTemplateTests(AuthedClientTestCase):
    def test_normal_mode_unchanged(self):
        page = self.client.get("/view_terms/")
        self.assertNotContains(page, 'id="demo-banner"')
        self.assertContains(page, 'href="/admin/"')
        self.assertContains(page, "Log out")
        self.assertContains(self.client.get("/sources/"), "/sources/add/")
        self.assertContains(self.client.get("/schedules/"), "/schedules/add/")


class DemoUrlOverrideTests(DemoTestCase):
    def test_item_source_form_discards_pinned_url_and_suffix(self):
        item = self.visitor_item(sources=())
        self.client.post(f"/item/{item.pk}/sources/add/", {
            "source": "tidewater",
            "pinned_url": "https://evil.example/",
            "url_suffix": "&x=1",
        })
        item_source = ItemSource.objects.get(item=item)
        self.assertEqual((item_source.pinned_url, item_source.url_suffix), ("", ""))

        self.client.post(f"/item_source/{item_source.pk}/edit/", {
            "source": "tidewater",
            "pinned_url": "https://evil.example/",
            "url_suffix": "&x=1",
        })
        item_source.refresh_from_db()
        self.assertEqual((item_source.pinned_url, item_source.url_suffix), ("", ""))

    def test_bulk_add_discards_pinned_url_and_suffix(self):
        self.client.post("/bulk_add/", {
            "tag": "__none__",
            "search_terms": "Starfall",
            "priority": "2",
            "metadata_provider_key": "",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-0-source": "dragonhoard",
            "form-0-pinned_url": "https://evil.example/",
            "form-0-url_suffix": "&x=1",
        })
        item_source = ItemSource.objects.get(item__text="Starfall")
        self.assertEqual((item_source.pinned_url, item_source.url_suffix), ("", ""))


class DemoPresetPatternTests(DemoTestCase):
    def test_preset_pattern_saves_and_filters(self):
        item = self.visitor_item(text="Cindermaw Tyrant", sources=())
        self.client.post(f"/item/{item.pk}/sources/add/", {
            "source": "dragonhoard", "title_exclude_patterns": ["Foil"],
        })
        self.assertEqual(ItemSource.objects.get(item=item).title_exclude_patterns, ["Foil"])
        DemoState.objects.filter(pk=1).update(last_update_started_at=None)
        self.client.post("/update/", {"mode": "selected", "item_ids": [item.pk]})
        titles = set(SearchResult.objects.filter(item=item).values_list("title", flat=True))
        self.assertTrue(titles)
        self.assertFalse(any("Foil" in title for title in titles))

    def test_arbitrary_regex_rejected_everywhere(self):
        bad = "(a+)+$"
        item = self.visitor_item(sources=())
        self.client.post(f"/item/{item.pk}/sources/add/", {
            "source": "dragonhoard", "title_include_patterns": [bad],
        })
        self.assertFalse(ItemSource.objects.filter(item=item).exists())

        item_source = ItemSource.objects.create(item=item, source_id="manavault")
        self.client.post(f"/item_source/{item_source.pk}/edit/", {
            "source": "manavault", "title_exclude_patterns": [bad],
        })
        item_source.refresh_from_db()
        self.assertEqual(item_source.title_exclude_patterns, [])

        self.client.post("/bulk_add/", {
            "tag": "__none__", "search_terms": "Regex Probe", "priority": "2",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-0-source": "dragonhoard", "form-0-title_include_patterns": [bad],
        })
        self.assertFalse(SearchableItem.objects.filter(text="Regex Probe").exists())

        self.client.post("/bulk_edit/", {
            "in_workspace": "1",
            "item_ids": [item.pk],
            "priority": "__leave__", "active": "__leave__", "metadata_provider_key": "__leave__",
            "source_include_manual:manavault": bad,
            f'source_include_state:["manavault", "{bad}"]': "add",
        })
        item_source.refresh_from_db()
        self.assertEqual(item_source.title_include_patterns, [])

    def test_bulk_edit_offers_presets_without_free_text(self):
        item = self.visitor_item(sources=("manavault",))
        response = self.client.post("/bulk_edit/", {"item_ids": [item.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "source_include_manual:")
        self.assertNotContains(response, "Add new pattern(s)")
        self.assertContains(response, "Playmat|Sleeves|Deck Box")


class DemoCapTests(DemoTestCase):
    @override_settings(DEMO_MAX_VISITOR_ITEMS=2)
    def test_item_cap(self):
        for text in ("One", "Two"):
            self.client.post("/add_term/", {"text": text})
        response = self.client.post("/add_term/", {"text": "Three"}, follow=True)
        self.assertFalse(SearchableItem.objects.filter(text="Three").exists())
        self.assertContains(response, "demo item limit")

    @override_settings(DEMO_MAX_VISITOR_ITEMS=2)
    def test_bulk_add_cap_is_all_or_nothing(self):
        response = self.client.post("/bulk_add/", {
            "tag": "__none__", "search_terms": "A\nB\nC", "priority": "2",
            "form-TOTAL_FORMS": "0", "form-INITIAL_FORMS": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SearchableItem.objects.filter(text__in=["A", "B", "C"]).exists())

    @override_settings(DEMO_BULK_ADD_MAX_TERMS=2)
    def test_demo_bulk_add_term_limit(self):
        self.client.post("/bulk_add/", {
            "tag": "__none__", "search_terms": "A\nB\nC", "priority": "2",
            "form-TOTAL_FORMS": "0", "form-INITIAL_FORMS": "0",
        })
        self.assertFalse(SearchableItem.objects.filter(text__in=["A", "B", "C"]).exists())

    @override_settings(DEMO_MAX_VISITOR_TAGS=1)
    def test_tag_cap(self):
        self.client.post("/tags/add/", {"name": "Mine", "color": ""})
        response = self.client.post("/tags/add/", {"name": "Another", "color": ""}, follow=True)
        self.assertTrue(Tag.objects.filter(name="Mine").exists())
        self.assertFalse(Tag.objects.filter(name="Another").exists())
        self.assertContains(response, "demo tag limit")

    def test_update_throttle_allows_one_run(self):
        DemoState.objects.filter(pk=1).update(last_update_started_at=None)
        before = WebUpdate.objects.count()
        self.client.post("/update/", {"mode": "all"})
        response = self.client.post("/update/", {"mode": "all"}, follow=True)
        self.assertEqual(WebUpdate.objects.count(), before + 1)
        self.assertContains(response, "try again in")

    @override_settings(DEMO_MAX_SEARCH_RESULTS=1)
    def test_search_result_ceiling_refuses_updates(self):
        DemoState.objects.filter(pk=1).update(last_update_started_at=None)
        before = WebUpdate.objects.count()
        response = self.client.post("/update/", {"mode": "all"}, follow=True)
        self.assertEqual(WebUpdate.objects.count(), before)
        self.assertContains(response, "updates are paused")


class DemoCsvExportTests(DemoTestCase):
    def _export_with_formula_term(self):
        item = self.visitor_item(text="=HYPERLINK(1)", sources=())
        update = WebUpdate.objects.create()
        SearchResult.objects.create(
            title="Vexing Starfall", search_term=item.text, price=1.0, item=item,
            update=update, source_id="dragonhoard",
        )
        return self.client.get(f"/item/{item.pk}/export.csv").content.decode()

    def test_formula_cells_escaped_in_demo_mode(self):
        self.assertIn("'=HYPERLINK(1)", self._export_with_formula_term())

    @override_settings(DEMO_MODE=False)
    def test_unchanged_outside_demo_mode(self):
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_user("operator", password="x"))
        content = self._export_with_formula_term()
        self.assertIn(",=HYPERLINK(1),", content)
        self.assertNotIn("'=HYPERLINK", content)
