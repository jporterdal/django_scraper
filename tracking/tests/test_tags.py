from django.test import TestCase
from django.urls import reverse

from tracking.models import ItemSource, Tag
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item, make_item_source, make_source


class TagFilterTests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gpu_tag = Tag.objects.create(name="GPU", color="#3498db")
        cls.mtg_tag = Tag.objects.create(name="MTG", color="#9b59b6")
        cls.gpu_item = make_item(text="rtx 5070")
        cls.mtg_item = make_item(text="lightning bolt")
        cls.gpu_item.tags.add(cls.gpu_tag)
        cls.mtg_item.tags.add(cls.mtg_tag)

    def test_list_shows_all_items_without_filter(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rtx 5070")
        self.assertContains(response, "lightning bolt")

    def test_list_filtered_by_tag(self):
        response = self.client.get(reverse("view_terms"), {"tag": self.gpu_tag.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rtx 5070")
        self.assertNotContains(response, "lightning bolt")

    def test_list_includes_tag_filter_buttons(self):
        response = self.client.get(reverse("view_terms"))
        self.assertContains(response, "Filter by tag:")
        self.assertContains(response, "GPU")
        self.assertContains(response, "MTG")
        self.assertContains(response, f"?tag={self.gpu_tag.pk}")

    def test_list_shows_tag_update_button_when_tag_has_updatable_items(self):
        source = make_source()
        make_item_source(self.gpu_item, source)
        self.gpu_item.active = True
        self.gpu_item.save()

        response = self.client.get(reverse("view_terms"), {"tag": self.gpu_tag.pk})
        self.assertContains(response, "Update items with this tag")
        self.assertContains(response, f'name="tag_id" value="{self.gpu_tag.pk}"')

    def test_list_shows_message_when_tag_has_no_updatable_items(self):
        response = self.client.get(reverse("view_terms"), {"tag": self.gpu_tag.pk})
        self.assertNotContains(response, "Update items with this tag")
        self.assertContains(
            response, "Active items with this tag have no configured sources"
        )

class TagManagementTests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tag = Tag.objects.create(name="GPU", color="#3498db")

    def test_tag_list_page(self):
        response = self.client.get(reverse("view_tags"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GPU")
        self.assertContains(response, "Add Tag")

    def test_create_tag(self):
        response = self.client.post(
            reverse("add_tag"),
            {"name": "MTG", "color": "#9b59b6"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Tag.objects.filter(name="MTG").exists())

    def test_edit_tag(self):
        response = self.client.post(
            reverse("edit_tag", args=[self.tag.pk]),
            {"name": "Graphics", "color": "#e74c3c"},
        )
        self.assertEqual(response.status_code, 302)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, "Graphics")

    def test_delete_tag(self):
        response = self.client.post(reverse("delete_tag", args=[self.tag.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tag.objects.filter(pk=self.tag.pk).exists())
