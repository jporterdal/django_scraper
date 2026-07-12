from django.test import TestCase
from django.urls import reverse
import json

from tracking.models import CC_DEFAULT_SEARCH_URL, ItemSource, Source
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_cc_source, make_item, make_item_source


class SourceModelTests(TestCase):
    def test_build_search_url_encodes_term(self):
        source = Source(
            key="cc",
            name="Canada Computers",
            base_search_url=CC_DEFAULT_SEARCH_URL,
        )
        url = source.build_search_url("rtx 5070")
        self.assertEqual(
            url,
            "https://www.canadacomputers.com/en/search?s=rtx+5070&pickup=62",
        )

    def test_build_search_url_appends_suffix(self):
        source = Source(
            key="cc",
            name="Canada Computers",
            base_search_url=CC_DEFAULT_SEARCH_URL,
        )
        url = source.build_search_url("widget", url_suffix="extra=1")
        self.assertIn("extra=1", url)
        self.assertIn("widget", url)

    def test_build_search_url_requires_term_placeholder(self):
        source = Source(
            key="xx",
            name="Bad",
            base_search_url="https://example.com/search",
        )
        with self.assertRaises(ValueError):
            source.build_search_url("widget")

class SourceJSONConfigTests(TestCase):
    def test_new_fields_have_expected_defaults(self):
        source = Source(
            key="cc",
            name="Canada Computers",
            base_search_url=CC_DEFAULT_SEARCH_URL,
        )
        self.assertEqual(source.request_headers, {})
        self.assertIsNone(source.page_size)
        self.assertEqual(source.parser_key, "")

    def test_parser_key_is_required_on_form(self):
        from tracking.forms import SourceForm

        form = SourceForm(data={
            "key": "zz",
            "name": "No Parser",
            "http_method": "GET",
            "base_search_url": "https://example.com/search?s={term}",
            "request_body_template": "{}",
            "request_headers": "{}",
            "max_pages": "1",
            "parser_key": "",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("parser_key", form.errors)

    def test_key_accepts_longer_value(self):
        source = Source.objects.create(
            key="storepass",
            name="Long Key Source",
            parser_key="cc",
            base_search_url="https://example.com/search?s={term}",
        )
        source.refresh_from_db()
        self.assertEqual(source.key, "storepass")

class SourceManagementTests(AuthedClientTestCase):
    """Phase 2 Step 7 — Source CRUD UI."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = make_cc_source()

    def _valid_data(self, **overrides):
        # Use a source key that does not already exist.
        data = {
            "key": "zz",
            "name": "New Store",
            "parser_key": "shopify",
            "http_method": "GET",
            "base_search_url": "https://example.com/search/keyword/{term}",
            "request_body_template": "{}",
            "request_headers": "{}",
            "page_size": "",
            "max_pages": "1",
        }
        data.update(overrides)
        return data

    def test_source_list_page(self):
        response = self.client.get(reverse("view_sources"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cc")
        self.assertContains(response, "Canada Computers")

    def test_create_source_valid(self):
        response = self.client.post(reverse("add_source"), self._valid_data())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Source.objects.filter(key="zz").exists())
        created = Source.objects.get(key="zz")
        self.assertEqual(created.parser_key, "shopify")

    def test_create_source_rejects_missing_term_for_get(self):
        response = self.client.post(
            reverse("add_source"),
            self._valid_data(base_search_url="https://example.com/search/keyword/foo"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Source.objects.filter(key="zz").exists())
        self.assertIn("base_search_url", response.context["form"].errors)

    def test_create_post_source_without_term_in_url(self):
        body = {
            "context": {"mode": "buy", "page": 1, "per_page": 24},
            "q": "{term}",
        }
        response = self.client.post(
            reverse("add_source"),
            self._valid_data(
                key="wt",
                name="POST JSON Store",
                parser_key="wtfilters",
                http_method="POST",
                base_search_url="https://example.com/api/search",
                request_body_template=json.dumps(body),
                request_headers=json.dumps(
                    {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Origin": "https://example.com",
                        "Referer": "https://example.com/search?q={term}",
                    }
                ),
            ),
        )
        self.assertEqual(response.status_code, 302)
        created = Source.objects.get(key="wt")
        self.assertEqual(created.http_method, Source.HttpMethod.POST)
        self.assertEqual(created.request_body_template["q"], "{term}")
        self.assertEqual(created.max_pages, 1)

    def test_edit_post_source_preserves_url_without_term(self):
        Source.objects.create(
            key="wt",
            name="POST JSON Store",
            parser_key="wtfilters",
            http_method=Source.HttpMethod.POST,
            base_search_url="https://example.com/api/search",
            request_body_template={"q": "{term}"},
        )
        response = self.client.post(
            reverse("edit_source", args=["wt"]),
            self._valid_data(
                key="wt",
                name="POST JSON Store (updated)",
                parser_key="wtfilters",
                http_method="POST",
                base_search_url="https://example.com/api/search",
                request_body_template='{"q": "{term}"}',
                request_headers="{}",
            ),
        )
        self.assertEqual(response.status_code, 302)
        updated = Source.objects.get(key="wt")
        self.assertEqual(updated.name, "POST JSON Store (updated)")

    def test_create_source_rejects_unknown_parser_key(self):
        response = self.client.post(
            reverse("add_source"),
            self._valid_data(parser_key="not_a_real_parser"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Source.objects.filter(key="zz").exists())
        self.assertIn("parser_key", response.context["form"].errors)

    def test_edit_source_key_disabled(self):
        response = self.client.get(reverse("edit_source", args=[self.source.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].fields["key"].disabled)

    def test_delete_source_confirm_shows_counts(self):
        item = make_item(text="an item")
        make_item_source(item, self.source)
        response = self.client.get(reverse("delete_source", args=[self.source.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["item_count"], 1)
        self.assertContains(response, "1 item-source link(s)")

    def test_delete_source_removes_it(self):
        response = self.client.post(reverse("delete_source", args=[self.source.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Source.objects.filter(key="cc").exists())
