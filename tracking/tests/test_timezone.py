from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.conf import settings
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tracking.models import SearchResult, WebUpdate
from tracking.tests.base import LinkedSourceTestCase
from tracking.tests.factories import make_search_result, make_web_update
from tracking.views import SearchableListView


class TimezoneDisplayTests(LinkedSourceTestCase):

    def test_settings_time_zone(self):
        self.assertEqual(settings.TIME_ZONE, "America/Halifax")

    def test_localtime_used_in_sparkline_json(self):
        utc_dt = datetime(2026, 1, 16, 3, 0, 0, tzinfo=ZoneInfo("UTC"))
        update = make_web_update()
        WebUpdate.objects.filter(pk=update.pk).update(timestamp=utc_dt)
        update.refresh_from_db()
        make_search_result(
            self.item,
            self.source,
            update,
            title="Test Product",
            price=19.99,
        )

        request = RequestFactory().get(reverse("view_terms"))
        view = SearchableListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        context = view.get_context_data()
        items = json.loads(context["items_json"])
        item_data = next(i for i in items if i["id"] == self.item.pk)
        self.assertEqual(item_data["price_history"][0]["date"], "15/01/26")

    def test_webupdate_list_shows_atlantic_time(self):
        utc_dt = datetime(2026, 1, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        update = WebUpdate.objects.create(result_count=42)
        WebUpdate.objects.filter(pk=update.pk).update(timestamp=utc_dt)
        update.refresh_from_db()

        response = self.client.get(reverse("view_updates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-01-15 14:00")
        self.assertNotContains(response, "2026-01-15 18:00")
        self.assertContains(response, "42")
