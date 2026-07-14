"""Latest Price source-key tag on view_terms."""

from django.urls import reverse

from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_item,
    make_item_source,
    make_search_result,
    make_source,
    make_web_update,
)


class ListPriceSourceTagTests(AuthedClientTestCase):
    """Sanity checks that Latest Price HTML includes a bracketed Source.key."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.src = make_source(
            key="amz",
            name="Source Amz",
            parser_key="shopify",
            base_search_url="https://example.com/s?q={term}",
        )
        cls.item = make_item(text="priced widget", active=True)
        make_item_source(cls.item, cls.src)

    def test_view_terms_shows_bracketed_source_key(self):
        update = make_web_update()
        make_search_result(
            self.item, self.src, update, title="Widget", price=70.0
        )
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "[amz]")

    def test_view_terms_no_price_omits_source_tag(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "—")
        # No Latest Price → no bracketed source key (avoid bare "[]" check:
        # items_json legitimately contains empty arrays).
        self.assertNotContains(response, "[amz]")
