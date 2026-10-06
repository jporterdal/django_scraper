"""demo-replay-data / demo-mode: demo metadata provider and inline drain (tasks 2.7, 2.8)."""

from django.test import SimpleTestCase, override_settings

from tracking.demo.provider import DemoMetadataProvider
from tracking.metadata_providers import (
    PROVIDERS,
    ResolutionStatus,
    ScryfallProvider,
    get_metadata_providers,
)
from tracking.models import ItemMetadata, MetadataFetchRequest, SearchableItem

from .demo_base import DemoTestCase


class RegistryAccessorTests(SimpleTestCase):
    def test_normal_mode_returns_real_registry(self):
        self.assertIs(get_metadata_providers(), PROVIDERS)
        self.assertIs(get_metadata_providers()["scryfall"], ScryfallProvider)

    @override_settings(DEMO_MODE=True)
    def test_demo_mode_offers_only_the_demo_provider(self):
        self.assertEqual(get_metadata_providers(), {"demo": DemoMetadataProvider})


class DemoProviderTests(SimpleTestCase):
    def _item(self, text):
        return SearchableItem(text=text)

    def test_unique_name_matches(self):
        result = DemoMetadataProvider().resolve(self._item("Vexing Starfall"))
        self.assertEqual(result.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.external_id, "demo-vexing-starfall")

    def test_ambiguous_term_needs_review(self):
        result = DemoMetadataProvider().resolve(self._item("Thornvault Sentinel"))
        self.assertEqual(result.status, ResolutionStatus.NEEDS_REVIEW)
        self.assertEqual(
            {c.external_id for c in result.candidates},
            {"demo-thornvault-sentinel", "demo-thornvault-sentinel-showcase"},
        )

    def test_unknown_term_no_match(self):
        result = DemoMetadataProvider().resolve(self._item("Not A Card"))
        self.assertEqual(result.status, ResolutionStatus.NO_MATCH)

    def test_fetch_by_id_only_known_ids(self):
        self.assertIsNotNone(DemoMetadataProvider().fetch_by_id("demo-gloomtide-archivist"))
        self.assertIsNone(DemoMetadataProvider().fetch_by_id("https://evil.example/"))

    def test_display_is_self_hosted(self):
        payload = DemoMetadataProvider().fetch_by_id("demo-cindermaw-tyrant")
        display = DemoMetadataProvider().to_display(payload)
        self.assertTrue(display["thumbnail_url"].startswith("/static/tracking/demo/"))
        self.assertEqual(display["external_url"], display["thumbnail_url"])
        self.assertIn("Dragon", display["description"])


class InlineDrainTests(DemoTestCase):
    def test_new_item_shows_metadata_immediately_without_warning(self):
        response = self.client.post(
            "/add_term/", {"text": "Hollowbrook Seer", "metadata_provider_key": "demo"}
        )
        self.assertEqual(response.status_code, 302)
        item = SearchableItem.objects.get(text="Hollowbrook Seer")
        self.assertEqual(item.metadata.status, ItemMetadata.Status.MATCHED)
        self.assertFalse(MetadataFetchRequest.objects.filter(status="pending").exists())

        detail = self.client.get(f"/item/{item.pk}/")
        self.assertContains(detail, "/static/tracking/demo/hollowbrook-seer.svg")
        self.assertNotContains(detail, "run_huey")

    def test_unknown_manual_id_is_no_match(self):
        item = self.visitor_item(text="Thornvault Sentinel")
        item.metadata_provider_key = "demo"
        item.save()
        self.client.post(f"/item/{item.pk}/metadata/set_external_id/", {"external_id": "nope"})
        self.assertEqual(ItemMetadata.objects.get(item=item).status, ItemMetadata.Status.NO_MATCH)
