"""item-metadata-enrichment — model/registry/entrypoint/task/view coverage.

No network: ``ScryfallProvider`` HTTP calls are mocked throughout. Huey runs in
immediate mode under the suite (see settings), so calling a periodic/task
function directly executes it inline — consistent with the rest of the suite
(see test_schedule.py).
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tracking import tasks
from tracking.forms import BULK_ADD_TAG_NONE
from tracking.metadata import (
    get_item_metadata,
    request_metadata_refresh,
    reset_item_metadata,
    sync_metadata_after_save,
)
from tracking.metadata_providers import (
    PROVIDERS,
    ResolutionStatus,
    ScryfallProvider,
)
from tracking.models import ItemMetadata, MetadataFetchRequest, SearchableItem
from tracking.parsers import JSONSearchParser
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item


# ---------------------------------------------------------------------------
# 8.1 — model / staleness-trigger tests
# ---------------------------------------------------------------------------


class SyncMetadataAfterSaveTests(TestCase):
    def test_provider_changed_resets_and_requests_refresh(self):
        item = make_item(metadata_provider_key="other")
        ItemMetadata.objects.create(
            item=item,
            status=ItemMetadata.Status.MATCHED,
            external_id="old-id",
            pinned_external_id="old-pin",
            payload={"name": "Old Card"},
        )

        sync_metadata_after_save(item, provider_changed=True, text_changed=False)

        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.UNFETCHED)
        self.assertEqual(item.metadata.external_id, "")
        self.assertEqual(item.metadata.pinned_external_id, "")
        self.assertEqual(item.metadata.payload, {})
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 1)

    def test_provider_cleared_resets_without_requesting_refresh(self):
        item = make_item(metadata_provider_key="")
        ItemMetadata.objects.create(
            item=item, status=ItemMetadata.Status.MATCHED, external_id="old", payload={"a": 1}
        )

        sync_metadata_after_save(item, provider_changed=True, text_changed=False)

        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.UNFETCHED)
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)

    def test_unpinned_text_change_requests_refresh(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.MATCHED)

        sync_metadata_after_save(item, provider_changed=False, text_changed=True)

        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 1)

    def test_pinned_text_change_does_not_request_refresh(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(
            item=item, status=ItemMetadata.Status.MATCHED, pinned_external_id="pinned-1"
        )

        sync_metadata_after_save(item, provider_changed=False, text_changed=True)

        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)

    def test_unrelated_change_does_not_request_refresh(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.MATCHED)

        sync_metadata_after_save(item, provider_changed=False, text_changed=False)

        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)

    def test_no_provider_no_text_change_is_a_no_op(self):
        item = make_item(metadata_provider_key="")

        sync_metadata_after_save(item, provider_changed=False, text_changed=True)

        self.assertFalse(ItemMetadata.objects.filter(item=item).exists())
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)


class MetadataHelperTests(TestCase):
    def test_get_item_metadata_returns_none_when_absent(self):
        item = make_item()
        self.assertIsNone(get_item_metadata(item))

    def test_get_item_metadata_returns_row_when_present(self):
        item = make_item()
        item_metadata = ItemMetadata.objects.create(item=item)
        self.assertEqual(get_item_metadata(item), item_metadata)

    def test_request_metadata_refresh_creates_metadata_and_request(self):
        item = make_item(metadata_provider_key="scryfall")
        request_metadata_refresh(item)
        self.assertTrue(ItemMetadata.objects.filter(item=item).exists())
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                item=item, status=MetadataFetchRequest.Status.PENDING
            ).count(),
            1,
        )

    def test_reset_item_metadata_clears_fetched_state(self):
        item = make_item()
        ItemMetadata.objects.create(
            item=item,
            status=ItemMetadata.Status.ERROR,
            external_id="x",
            pinned_external_id="y",
            payload={"a": 1},
        )
        reset_item_metadata(item)
        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.UNFETCHED)
        self.assertEqual(item.metadata.external_id, "")
        self.assertEqual(item.metadata.pinned_external_id, "")
        self.assertEqual(item.metadata.payload, {})


# ---------------------------------------------------------------------------
# 8.2 — registry / ScryfallProvider tests
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, data=None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = data
    return response


class ScryfallProviderTests(TestCase):
    def setUp(self):
        self.provider = ScryfallProvider()
        self.item = make_item(text="Lightning Bolt")

    @patch("tracking.metadata_providers.requests.get")
    def test_resolve_single_card_is_matched(self, mock_get):
        mock_get.return_value = _mock_response(
            data={
                "data": [
                    {
                        "id": "abc123",
                        "name": "Lightning Bolt",
                        "oracle_text": "Deal 3 damage.",
                        "scryfall_uri": "https://scryfall.com/card/abc123",
                        "image_uris": {"normal": "https://img/normal.png"},
                    }
                ]
            }
        )

        result = self.provider.resolve(self.item)

        self.assertEqual(result.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.external_id, "abc123")
        self.assertEqual(result.payload["name"], "Lightning Bolt")

    @patch("tracking.metadata_providers.requests.get")
    def test_resolve_sends_descriptive_user_agent(self, mock_get):
        mock_get.return_value = _mock_response(data={"data": []})

        self.provider.resolve(self.item)

        _, kwargs = mock_get.call_args
        user_agent = kwargs["headers"]["User-Agent"]
        self.assertIn("django-scraper", user_agent)
        self.assertNotIn("Mozilla", user_agent)

    @patch("tracking.metadata_providers.requests.get")
    def test_resolve_multiple_cards_is_needs_review(self, mock_get):
        mock_get.return_value = _mock_response(
            data={
                "data": [
                    {"id": "abc123", "name": "Card A"},
                    {"id": "def456", "name": "Card B"},
                ]
            }
        )

        result = self.provider.resolve(self.item)

        self.assertEqual(result.status, ResolutionStatus.NEEDS_REVIEW)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0].external_id, "abc123")
        self.assertEqual(result.candidates[1].external_id, "def456")

    @patch("tracking.metadata_providers.requests.get")
    def test_resolve_zero_cards_is_no_match(self, mock_get):
        mock_get.return_value = _mock_response(data={"data": []})

        result = self.provider.resolve(self.item)

        self.assertEqual(result.status, ResolutionStatus.NO_MATCH)

    @patch("tracking.metadata_providers.requests.get")
    def test_resolve_404_is_no_match(self, mock_get):
        mock_get.return_value = _mock_response(status_code=404)

        result = self.provider.resolve(self.item)

        self.assertEqual(result.status, ResolutionStatus.NO_MATCH)

    @patch("tracking.metadata_providers.requests.get")
    def test_fetch_by_id_returns_payload(self, mock_get):
        mock_get.return_value = _mock_response(data={"id": "abc123", "name": "Lightning Bolt"})

        payload = self.provider.fetch_by_id("abc123")

        self.assertEqual(payload["name"], "Lightning Bolt")

    @patch("tracking.metadata_providers.requests.get")
    def test_fetch_by_id_404_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(status_code=404)

        self.assertIsNone(self.provider.fetch_by_id("nope"))

    def test_to_display_maps_image_description_url(self):
        payload = {
            "image_uris": {"normal": "https://img/normal.png"},
            "oracle_text": "Deal 3 damage to any target.",
            "scryfall_uri": "https://scryfall.com/card/abc123",
        }

        display = self.provider.to_display(payload)

        self.assertEqual(display["thumbnail_url"], "https://img/normal.png")
        self.assertEqual(display["description"], "Deal 3 damage to any target.")
        self.assertEqual(display["external_url"], "https://scryfall.com/card/abc123")

    def test_to_display_falls_back_to_card_faces(self):
        payload = {
            "card_faces": [
                {"image_uris": {"normal": "https://img/front.png"}, "oracle_text": "Front text."},
                {"oracle_text": "Back text."},
            ],
        }

        display = self.provider.to_display(payload)

        self.assertEqual(display["thumbnail_url"], "https://img/front.png")
        self.assertIn("Front text.", display["description"])
        self.assertIn("Back text.", display["description"])

    def test_to_display_handles_empty_payload(self):
        self.assertEqual(
            self.provider.to_display({}),
            {"thumbnail_url": "", "description": "", "external_url": ""},
        )

    def test_registered_in_provider_registry(self):
        self.assertIs(PROVIDERS["scryfall"], ScryfallProvider)


# ---------------------------------------------------------------------------
# 8.3 — shared-entrypoint wiring tests (create/bulk-create/edit)
# ---------------------------------------------------------------------------


class MetadataEntrypointWiringTests(AuthedClientTestCase):
    def test_single_create_with_provider_enqueues_refresh(self):
        response = self.client.post(
            reverse("add_term"), {"text": "Lightning Bolt", "metadata_provider_key": "scryfall"}
        )
        self.assertEqual(response.status_code, 302)
        item = SearchableItem.objects.get(text="Lightning Bolt")
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                item=item, status=MetadataFetchRequest.Status.PENDING
            ).count(),
            1,
        )
        self.assertEqual(item.metadata.status, ItemMetadata.Status.UNFETCHED)

    def test_single_create_without_provider_does_not_enqueue(self):
        response = self.client.post(
            reverse("add_term"), {"text": "Plain Item", "metadata_provider_key": ""}
        )
        self.assertEqual(response.status_code, 302)
        item = SearchableItem.objects.get(text="Plain Item")
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)
        self.assertFalse(ItemMetadata.objects.filter(item=item).exists())

    def test_bulk_create_enqueues_for_every_item_with_provider(self):
        response = self.client.post(
            reverse("bulk_add"),
            {
                "tag": BULK_ADD_TAG_NONE,
                "search_terms": "Card A\nCard B\nCard C",
                "priority": str(SearchableItem.Priority.B),
                "metadata_provider_key": "scryfall",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-source": "",
                "form-0-url_suffix": "",
                "form-0-pinned_url": "",
                "form-0-title_include_patterns": "",
                "form-0-title_exclude_patterns": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SearchableItem.objects.count(), 3)
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                status=MetadataFetchRequest.Status.PENDING
            ).count(),
            3,
        )

    def test_bulk_create_without_provider_does_not_enqueue(self):
        response = self.client.post(
            reverse("bulk_add"),
            {
                "tag": BULK_ADD_TAG_NONE,
                "search_terms": "Card A\nCard B",
                "priority": str(SearchableItem.Priority.B),
                "metadata_provider_key": "",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-source": "",
                "form-0-url_suffix": "",
                "form-0-pinned_url": "",
                "form-0-title_include_patterns": "",
                "form-0-title_exclude_patterns": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MetadataFetchRequest.objects.count(), 0)

    def test_edit_changing_provider_resets_and_requests(self):
        item = make_item(text="Card", metadata_provider_key="scryfall")
        ItemMetadata.objects.create(
            item=item, status=ItemMetadata.Status.MATCHED, external_id="old"
        )

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": str(SearchableItem.Priority.B),
                "active": "on",
                "metadata_provider_key": "",
                "tags": [],
            },
        )

        self.assertEqual(response.status_code, 302)
        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.UNFETCHED)
        self.assertEqual(item.metadata.external_id, "")
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)

    def test_edit_text_change_enqueues_when_unpinned(self):
        item = make_item(text="Old Text", metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.MATCHED)

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": "New Text",
                "priority": str(SearchableItem.Priority.B),
                "active": "on",
                "metadata_provider_key": "scryfall",
                "tags": [],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                item=item, status=MetadataFetchRequest.Status.PENDING
            ).count(),
            1,
        )

    def test_edit_text_change_does_not_enqueue_when_pinned(self):
        item = make_item(text="Old Text", metadata_provider_key="scryfall")
        ItemMetadata.objects.create(
            item=item, status=ItemMetadata.Status.MATCHED, pinned_external_id="pinned-1"
        )

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": "New Text",
                "priority": str(SearchableItem.Priority.B),
                "active": "on",
                "metadata_provider_key": "scryfall",
                "tags": [],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)

    def test_edit_unrelated_field_does_not_enqueue(self):
        item = make_item(text="Card", metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.MATCHED)

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": str(SearchableItem.Priority.A),
                "active": "on",
                "metadata_provider_key": "scryfall",
                "tags": [],
            },
        )

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.priority, SearchableItem.Priority.A)
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)


# ---------------------------------------------------------------------------
# 8.4 — periodic drain-task tests
# ---------------------------------------------------------------------------


class _StubProvider:
    """No-network stand-in registered under 'stub' for periodic-task tests."""

    def resolve(self, item):
        from tracking.metadata_providers import ResolutionResult

        return ResolutionResult(
            status=ResolutionStatus.MATCHED,
            external_id=f"stub-{item.pk}",
            payload={"name": item.text},
        )

    def to_display(self, payload):
        return {
            "thumbnail_url": f"https://example.com/{payload.get('name', '')}.png",
            "description": payload.get("name", ""),
            "external_url": "https://example.com",
        }

    def fetch_by_id(self, external_id):
        return {"name": external_id}


class DrainMetadataFetchQueueTests(TestCase):
    def test_drain_respects_batch_size_across_multiple_wakes(self):
        with patch.dict("tracking.metadata_providers.PROVIDERS", {"stub": _StubProvider}):
            item_count = tasks.METADATA_DRAIN_BATCH_SIZE + 5
            items = [
                make_item(text=f"item {i}", metadata_provider_key="stub")
                for i in range(item_count)
            ]
            for item in items:
                request_metadata_refresh(item)

            self.assertEqual(
                MetadataFetchRequest.objects.filter(
                    status=MetadataFetchRequest.Status.PENDING
                ).count(),
                item_count,
            )

            processed_first = tasks.drain_pending_metadata_fetch_requests()
            self.assertEqual(processed_first, tasks.METADATA_DRAIN_BATCH_SIZE)
            self.assertEqual(
                MetadataFetchRequest.objects.filter(
                    status=MetadataFetchRequest.Status.PENDING
                ).count(),
                item_count - tasks.METADATA_DRAIN_BATCH_SIZE,
            )
            self.assertEqual(
                MetadataFetchRequest.objects.filter(
                    status=MetadataFetchRequest.Status.DONE
                ).count(),
                tasks.METADATA_DRAIN_BATCH_SIZE,
            )

            processed_second = tasks.drain_pending_metadata_fetch_requests()
            self.assertEqual(processed_second, item_count - tasks.METADATA_DRAIN_BATCH_SIZE)
            self.assertEqual(
                MetadataFetchRequest.objects.filter(
                    status=MetadataFetchRequest.Status.PENDING
                ).count(),
                0,
            )

            for item in items:
                item.metadata.refresh_from_db()
                self.assertEqual(item.metadata.status, ItemMetadata.Status.MATCHED)

    def test_drain_with_nothing_pending_is_a_no_op(self):
        self.assertEqual(tasks.drain_pending_metadata_fetch_requests(), 0)

    def test_fetch_metadata_unregistered_provider_sets_error(self):
        item = make_item(metadata_provider_key="not-a-real-provider")
        request_metadata_refresh(item)
        fetch_request = MetadataFetchRequest.objects.get(item=item)

        tasks.fetch_metadata(fetch_request.pk)

        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.ERROR)
        fetch_request.refresh_from_db()
        self.assertEqual(fetch_request.status, MetadataFetchRequest.Status.DONE)

    def test_fetch_metadata_uses_pinned_external_id_via_fetch_by_id(self):
        with patch.dict("tracking.metadata_providers.PROVIDERS", {"stub": _StubProvider}):
            item = make_item(metadata_provider_key="stub")
            item_metadata = ItemMetadata.objects.create(
                item=item, pinned_external_id="manual-id-1"
            )
            fetch_request = MetadataFetchRequest.objects.create(item=item)

            tasks.fetch_metadata(fetch_request.pk)

            item_metadata.refresh_from_db()
            self.assertEqual(item_metadata.status, ItemMetadata.Status.MATCHED)
            self.assertEqual(item_metadata.external_id, "manual-id-1")
            self.assertEqual(item_metadata.payload, {"name": "manual-id-1"})


# ---------------------------------------------------------------------------
# 10.4 — drain_metadata_queue management command
# ---------------------------------------------------------------------------


class DrainMetadataQueueCommandTests(TestCase):
    def test_command_processes_pending_requests(self):
        with patch.dict("tracking.metadata_providers.PROVIDERS", {"stub": _StubProvider}):
            item = make_item(metadata_provider_key="stub")
            request_metadata_refresh(item)
            self.assertEqual(
                MetadataFetchRequest.objects.filter(
                    status=MetadataFetchRequest.Status.PENDING
                ).count(),
                1,
            )

            out = StringIO()
            call_command("drain_metadata_queue", stdout=out)

            self.assertEqual(
                MetadataFetchRequest.objects.filter(
                    status=MetadataFetchRequest.Status.PENDING
                ).count(),
                0,
            )
            item.metadata.refresh_from_db()
            self.assertEqual(item.metadata.status, ItemMetadata.Status.MATCHED)
            self.assertIn("Drained 1 metadata fetch request", out.getvalue())

    def test_command_with_nothing_pending_runs_cleanly(self):
        out = StringIO()
        call_command("drain_metadata_queue", stdout=out)

        self.assertIn("No pending metadata fetch requests", out.getvalue())


# ---------------------------------------------------------------------------
# 8.5 — view tests (candidate selection, manual retry)
# ---------------------------------------------------------------------------


class MetadataViewActionTests(AuthedClientTestCase):
    def test_candidate_selection_sets_pinned_and_matched(self):
        item = make_item(metadata_provider_key="scryfall")
        item_metadata = ItemMetadata.objects.create(
            item=item,
            status=ItemMetadata.Status.NEEDS_REVIEW,
            payload={
                "candidates": [
                    {"external_id": "abc", "payload": {"name": "Card A"}},
                    {"external_id": "def", "payload": {"name": "Card B"}},
                ]
            },
        )

        response = self.client.post(
            reverse("metadata_select_candidate", args=[item.pk]), {"external_id": "def"}
        )

        self.assertEqual(response.status_code, 302)
        item_metadata.refresh_from_db()
        self.assertEqual(item_metadata.pinned_external_id, "def")
        self.assertEqual(item_metadata.external_id, "def")
        self.assertEqual(item_metadata.status, ItemMetadata.Status.MATCHED)
        self.assertEqual(item_metadata.payload, {"name": "Card B"})

    def test_candidate_selection_unknown_id_shows_error(self):
        item = make_item(metadata_provider_key="scryfall")
        item_metadata = ItemMetadata.objects.create(
            item=item,
            status=ItemMetadata.Status.NEEDS_REVIEW,
            payload={"candidates": [{"external_id": "abc", "payload": {}}]},
        )

        response = self.client.post(
            reverse("metadata_select_candidate", args=[item.pk]), {"external_id": "nope"}
        )

        self.assertEqual(response.status_code, 302)
        item_metadata.refresh_from_db()
        self.assertEqual(item_metadata.status, ItemMetadata.Status.NEEDS_REVIEW)

    def test_manual_retry_reenqueues_from_error(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.ERROR)

        response = self.client.post(reverse("metadata_retry", args=[item.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                item=item, status=MetadataFetchRequest.Status.PENDING
            ).count(),
            1,
        )

    def test_manual_retry_reenqueues_from_no_match(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.NO_MATCH)

        response = self.client.post(reverse("metadata_retry", args=[item.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                item=item, status=MetadataFetchRequest.Status.PENDING
            ).count(),
            1,
        )

    def test_manual_external_id_entry_pins_and_enqueues(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.NO_MATCH)

        response = self.client.post(
            reverse("metadata_set_external_id", args=[item.pk]), {"external_id": "manual-42"}
        )

        self.assertEqual(response.status_code, 302)
        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.pinned_external_id, "manual-42")
        self.assertEqual(
            MetadataFetchRequest.objects.filter(
                item=item, status=MetadataFetchRequest.Status.PENDING
            ).count(),
            1,
        )

    def test_item_detail_renders_matched_metadata(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(
            item=item,
            status=ItemMetadata.Status.MATCHED,
            payload={
                "oracle_text": "Deal 3 damage.",
                "scryfall_uri": "https://scryfall.com/card/abc",
                "image_uris": {"normal": "https://img/normal.png"},
            },
        )

        response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://img/normal.png")
        self.assertContains(response, "Deal 3 damage.")


# ---------------------------------------------------------------------------
# 10.1 — "fetch may not be processed" warning
# ---------------------------------------------------------------------------

WARNING_COPY = "Metadata fetch may not be processed"


class MetadataConsumerWarningTests(AuthedClientTestCase):
    def _huey_with_immediate(self, immediate):
        from django.conf import settings

        huey = dict(settings.HUEY)
        huey["immediate"] = immediate
        return huey

    def test_warning_shown_for_unfetched_under_default_test_settings(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.UNFETCHED)

        response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertContains(response, WARNING_COPY)

    def test_warning_shown_for_pending_under_default_test_settings(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.PENDING)

        response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertContains(response, WARNING_COPY)

    def test_warning_hidden_for_matched_status(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(
            item=item, status=ItemMetadata.Status.MATCHED, payload={"name": "Card"}
        )

        response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertNotContains(response, WARNING_COPY)

    def test_warning_hidden_for_error_status(self):
        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.ERROR)

        response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertNotContains(response, WARNING_COPY)

    def test_warning_hidden_when_settings_do_not_trigger_heuristic(self):
        from django.test import override_settings

        item = make_item(metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.UNFETCHED)

        with override_settings(
            REDIS_URL="redis://example.internal:6379/0",
            HUEY=self._huey_with_immediate(False),
        ):
            response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertNotContains(response, WARNING_COPY)

    def test_warning_hidden_with_no_provider_configured(self):
        item = make_item(metadata_provider_key="")

        response = self.client.get(reverse("item_detail", args=[item.pk]))

        self.assertNotContains(response, WARNING_COPY)


# ---------------------------------------------------------------------------
# 8.6 — list-view query-count test
# ---------------------------------------------------------------------------


class ListViewThumbnailQueryCountTests(AuthedClientTestCase):
    def _make_items_with_matched_metadata(self, n):
        for i in range(n):
            item = make_item(text=f"card {i}", metadata_provider_key="scryfall")
            ItemMetadata.objects.create(
                item=item,
                status=ItemMetadata.Status.MATCHED,
                payload={
                    "image_uris": {"normal": f"https://example.com/{i}.png"},
                    "scryfall_uri": "https://scryfall.com",
                },
            )

    def test_query_count_does_not_scale_with_item_count(self):
        self._make_items_with_matched_metadata(3)
        with CaptureQueriesContext(connection) as small:
            response_small = self.client.get(reverse("view_terms"))
        self.assertEqual(response_small.status_code, 200)

        SearchableItem.objects.all().delete()

        self._make_items_with_matched_metadata(15)
        with CaptureQueriesContext(connection) as large:
            response_large = self.client.get(reverse("view_terms"))
        self.assertEqual(response_large.status_code, 200)

        self.assertEqual(len(small.captured_queries), len(large.captured_queries))
        self.assertContains(response_large, "https://example.com/7.png")


# ---------------------------------------------------------------------------
# 8.7 — regression: metadata must not affect relevance filtering
# ---------------------------------------------------------------------------


class MetadataDoesNotAffectFilteringTests(TestCase):
    def test_filtering_behavior_unaffected_by_metadata_status(self):
        item = make_item(text="Lightning Bolt")
        item.expected_product_line = ["Magic"]
        item.save()

        for status in ItemMetadata.Status.values:
            ItemMetadata.objects.update_or_create(item=item, defaults={"status": status})

            accepted = JSONSearchParser(
                term=item.text,
                expected_product_line=item.expected_product_line,
                expected_category=item.expected_category,
            )
            accepted.add_result(
                title="Lightning Bolt", price=1.0, instock=True, category="", product_line="Magic"
            )
            self.assertEqual(len(accepted.results), 1, f"status={status}")

            rejected = JSONSearchParser(
                term=item.text,
                expected_product_line=item.expected_product_line,
                expected_category=item.expected_category,
            )
            rejected.add_result(
                title="Lightning Bolt",
                price=1.0,
                instock=True,
                category="",
                product_line="Pokemon",
            )
            self.assertEqual(len(rejected.results), 0, f"status={status}")
