from django.contrib.messages import get_messages
from django.urls import reverse
from unittest.mock import patch

from tracking.models import ItemSource, Tag, WebUpdate
from tracking.tests.base import LinkedSourceTestCase


class UpdateFromWebViewTests(LinkedSourceTestCase):
    # Phase 3 Step 3 — the view now enqueues run_web_update_task (Huey) after
    # creating a WebUpdate, instead of calling SearchResult.update_from_web
    # synchronously. These assertions were updated to the background flow: we
    # patch the task at the view boundary and check enqueue args + messages.
    def test_get_redirects_without_scraping(self):
        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.get(reverse("update"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("view_terms"))
        mock_task.assert_not_called()

    def test_post_all_active_enqueues_task_without_item_filter(self):
        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(reverse("update"), {"mode": "all"})

        self.assertEqual(response.status_code, 302)
        mock_task.assert_called_once()
        self.assertIsNone(mock_task.call_args.kwargs["item_ids"])
        # A WebUpdate row is pre-created and its pk passed to the task.
        webupdate = WebUpdate.objects.get()
        self.assertEqual(mock_task.call_args.args[0], webupdate.pk)
        self.assertEqual(webupdate.status, WebUpdate.Status.PENDING)
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Started a background price update", str(messages[0]))

    def test_post_selected_passes_filtered_item_ids(self):
        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_task.call_args.kwargs["item_ids"], [self.item.pk])

    def test_post_selected_with_no_checkboxes_shows_warning(self):
        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(reverse("update"), {"mode": "selected"})

        self.assertEqual(response.status_code, 302)
        mock_task.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(str(messages[0]), "No items selected.")

    def test_post_selected_inactive_items_shows_warning(self):
        self.item.active = False
        self.item.save()

        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        mock_task.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No active items in selection", str(messages[0]))

    def test_post_with_no_configured_sources_shows_warning(self):
        ItemSource.objects.all().delete()

        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(reverse("update"), {"mode": "all"})

        mock_task.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No items with configured sources", str(messages[0]))

    def test_post_tag_passes_filtered_item_ids(self):
        tag = Tag.objects.create(name="GPU")
        self.item.tags.add(tag)

        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(
                reverse("update"),
                {"mode": "tag", "tag_id": str(tag.pk)},
            )

        self.assertEqual(response.status_code, 302)
        mock_task.assert_called_once()
        self.assertEqual(mock_task.call_args.kwargs["item_ids"], [self.item.pk])

    def test_post_tag_with_no_active_items_shows_warning(self):
        tag = Tag.objects.create(name="Empty")
        self.item.active = False
        self.item.save()

        with patch("tracking.views.run_web_update_task") as mock_task:
            response = self.client.post(
                reverse("update"),
                {"mode": "tag", "tag_id": str(tag.pk)},
            )

        mock_task.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No active items with this tag", str(messages[0]))
