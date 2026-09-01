"""Assertions for the streamlined view_terms card header and grouped nav (view-terms-actions)."""

from django.test import TestCase
from django.urls import reverse

from tracking.models import Tag
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item


class ViewTermsCardHeaderTests(AuthedClientTestCase):
    def test_card_header_omits_nav_duplicating_links(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        header_start = content.index('<h4 class="mb-0">All Items</h4>')
        header_end = content.index("card-body")
        card_header = content[header_start:header_end]

        self.assertNotIn(">Bulk Add<", card_header)
        self.assertNotIn(">Manage Tags<", card_header)
        self.assertNotIn(">Manage Sources<", card_header)
        self.assertNotIn(">Schedules<", card_header)
        self.assertNotIn(">Scrape History<", card_header)
        self.assertIn(reverse("add_term"), card_header)

    def test_card_header_has_add_item_and_update_controls(self):
        response = self.client.get(reverse("view_terms"))
        self.assertContains(response, "Add New Item")
        self.assertContains(response, "Update Selected")
        self.assertContains(response, "Update All Active")

    def test_update_controls_submit_expected_mode_values(self):
        response = self.client.get(reverse("view_terms"))
        content = response.content.decode()

        update_url = reverse("update")
        self.assertIn(f'action="{update_url}"', content)
        self.assertIn('name="mode" value="selected"', content)
        self.assertIn('name="mode" value="all"', content)


class BaseNavTests(AuthedClientTestCase):
    def test_nav_exposes_add_menu(self):
        response = self.client.get(reverse("view_terms"))
        content = response.content.decode()

        self.assertIn(reverse("add_term"), content)
        self.assertIn(reverse("bulk_add"), content)

    def test_nav_exposes_manage_menu(self):
        response = self.client.get(reverse("view_terms"))
        content = response.content.decode()

        self.assertIn(reverse("view_tags"), content)
        self.assertIn(reverse("view_sources"), content)
        self.assertIn(reverse("view_schedules"), content)

    def test_nav_includes_logo_with_alt_text(self):
        response = self.client.get(reverse("view_terms"))
        content = response.content.decode()

        self.assertIn("tracking/images/logo.png", content)
        self.assertIn('alt="Pricing Tracker"', content)

    def test_other_page_also_gets_grouped_nav_and_logo(self):
        response = self.client.get(reverse("view_tags"))
        content = response.content.decode()

        self.assertIn(reverse("add_term"), content)
        self.assertIn(reverse("view_sources"), content)
        self.assertIn("tracking/images/logo.png", content)


class TagFilterRowContextualLinkTests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tag = Tag.objects.create(name="GPU", color="#3498db")
        cls.item = make_item(text="rtx 5070")
        cls.item.tags.add(cls.tag)

    def test_tag_filter_row_keeps_contextual_manage_tags_link(self):
        response = self.client.get(reverse("view_terms"))
        self.assertContains(response, "Manage tags")
        self.assertContains(response, reverse("view_tags"))
