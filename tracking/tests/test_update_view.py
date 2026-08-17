from django.contrib.messages import get_messages
from django.urls import reverse
from unittest.mock import patch

from tracking.locks import reset_in_memory_lock
from tracking.models import ItemSource, Tag, WebUpdate
from tracking.tests.base import LinkedSourceTestCase


class UpdateFromWebViewTests(LinkedSourceTestCase):
    # Fan-out (D8) — the view now calls dispatch_fan_out, which creates the
    # WebUpdate and enqueues one fetch_one Huey task per ItemSource, instead
    # of one run_web_update_task looping every item-source. These tests patch
    # tracking.tasks.fetch_one (the per-unit task) so dispatch_fan_out really
    # runs (and really creates the WebUpdate row) while no HTTP/parsing work
    # happens. Mocking fetch_one means the enqueue-time lock it would
    # normally release on terminalize is never released, so reset the
    # in-memory lock singleton each test (D11/task 5.5) — otherwise a lock
    # acquired here with a >=300s TTL can still read as held in a later test
    # that reuses the same (small, rollback-reset) pks.
    def setUp(self):
        super().setUp()
        reset_in_memory_lock()

    def test_get_redirects_without_scraping(self):
        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.get(reverse("update"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("view_terms"))
        mock_fetch_one.assert_not_called()

    def test_post_all_active_enqueues_task_without_item_filter(self):
        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(reverse("update"), {"mode": "all"})

        self.assertEqual(response.status_code, 302)
        # LinkedSourceTestCase wires exactly one ItemSource.
        mock_fetch_one.assert_called_once()
        webupdate = WebUpdate.objects.get()
        self.assertEqual(
            mock_fetch_one.call_args.args,
            (webupdate.pk, self.item_source.pk),
        )
        self.assertEqual(mock_fetch_one.call_args.kwargs["attempt"], 0)
        self.assertEqual(webupdate.status, WebUpdate.Status.PENDING)
        self.assertEqual(webupdate.total_searches, 1)
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Started a background price update", str(messages[0]))

    def test_post_selected_passes_filtered_item_ids(self):
        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        self.assertEqual(response.status_code, 302)
        webupdate = WebUpdate.objects.get()
        mock_fetch_one.assert_called_once_with(
            webupdate.pk, self.item_source.pk, attempt=0
        )

    def test_post_selected_with_no_checkboxes_shows_warning(self):
        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(reverse("update"), {"mode": "selected"})

        self.assertEqual(response.status_code, 302)
        mock_fetch_one.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(str(messages[0]), "No items selected.")

    def test_post_selected_inactive_items_shows_warning(self):
        self.item.active = False
        self.item.save()

        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        mock_fetch_one.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No active items in selection", str(messages[0]))

    def test_post_with_no_configured_sources_shows_warning(self):
        ItemSource.objects.all().delete()

        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(reverse("update"), {"mode": "all"})

        mock_fetch_one.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No items with configured sources", str(messages[0]))

    def test_post_tag_passes_filtered_item_ids(self):
        tag = Tag.objects.create(name="GPU")
        self.item.tags.add(tag)

        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(
                reverse("update"),
                {"mode": "tag", "tag_id": str(tag.pk)},
            )

        self.assertEqual(response.status_code, 302)
        webupdate = WebUpdate.objects.get()
        mock_fetch_one.assert_called_once_with(
            webupdate.pk, self.item_source.pk, attempt=0
        )

    def test_post_tag_with_no_active_items_shows_warning(self):
        tag = Tag.objects.create(name="Empty")
        self.item.active = False
        self.item.save()

        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            response = self.client.post(
                reverse("update"),
                {"mode": "tag", "tag_id": str(tag.pk)},
            )

        mock_fetch_one.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No active items with this tag", str(messages[0]))
