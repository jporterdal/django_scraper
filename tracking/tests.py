from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from .models import ItemSource, SearchableItem, Source


class UpdateFromWebViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.source = Source.objects.create(name="Test Source", key="cc")
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)

    def test_get_redirects_without_scraping(self):
        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.get(reverse("update"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("view_terms"))
        mock_update.assert_not_called()

    def test_post_all_active_calls_update_without_item_filter(self):
        with patch(
            "tracking.views.SearchResult.update_from_web",
            return_value=3,
        ) as mock_update:
            response = self.client.post(reverse("update"), {"mode": "all"})

        self.assertEqual(response.status_code, 302)
        mock_update.assert_called_once_with(items=None)
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Stored 3 price result(s)", str(messages[0]))

    def test_post_selected_passes_filtered_items(self):
        with patch(
            "tracking.views.SearchResult.update_from_web",
            return_value=1,
        ) as mock_update:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        self.assertEqual(response.status_code, 302)
        items_arg = mock_update.call_args.kwargs["items"]
        self.assertEqual(list(items_arg.values_list("pk", flat=True)), [self.item.pk])

    def test_post_selected_with_no_checkboxes_shows_warning(self):
        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.post(reverse("update"), {"mode": "selected"})

        self.assertEqual(response.status_code, 302)
        mock_update.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(str(messages[0]), "No items selected.")

    def test_post_selected_inactive_items_shows_warning(self):
        self.item.active = False
        self.item.save()

        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        mock_update.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No active items in selection", str(messages[0]))

    def test_post_with_no_configured_sources_shows_warning(self):
        ItemSource.objects.all().delete()

        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.post(reverse("update"), {"mode": "all"})

        mock_update.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No items with configured sources", str(messages[0]))
