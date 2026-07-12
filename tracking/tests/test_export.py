import json

from django.test import TestCase
from django.urls import reverse

from tracking.models import SearchableItem, SearchResult, WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_item,
    make_search_result,
    make_source,
    make_web_update,
)
from tracking.views import EXPORT_FIELDNAMES


class ExportTests(AuthedClientTestCase):
    """Phase 3 Step 6 — per-item CSV/JSON price-history export endpoints."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source_cc = make_source(
            key="cc",
            name="Canada Computers",
            parser_key="cc",
            base_search_url="https://example.com/search?s={term}",
        )
        cls.source_f2f = make_source(
            key="f2f",
            name="Face to Face",
            parser_key="shopify",
            base_search_url="https://example.com/f2f?q={term}",
        )
        cls.item = make_item(text="rtx 5070", active=True)
        cls.empty_item = make_item(text="no results", active=True)

        cls.update = make_web_update()

        make_search_result(
            cls.item,
            cls.source_cc,
            cls.update,
            title="ASUS RTX 5070",
            search_term="rtx 5070",
            price=799.99,
            category="GPUs",
            instock=1,
        )
        make_search_result(
            cls.item,
            cls.source_f2f,
            cls.update,
            title="MSI RTX 5070",
            search_term="rtx 5070",
            price=None,
            category="Video Cards",
            instock=0,
        )

    def test_csv_export_ok(self):
        response = self.client.get(reverse("export_item_csv", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"item-{self.item.pk}-price-history.csv", response["Content-Disposition"])

        body = response.content.decode("utf-8")
        lines = body.splitlines()
        # Header + one row per SearchResult.
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0].split(","), EXPORT_FIELDNAMES)
        self.assertIn("cc", body)
        self.assertIn("ASUS RTX 5070", body)
        self.assertIn("799.99", body)
        self.assertIn("MSI RTX 5070", body)

    def test_json_export_ok(self):
        response = self.client.get(reverse("export_item_json", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"item-{self.item.pk}-price-history.json", response["Content-Disposition"])

        data = json.loads(response.content)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        for row in data:
            self.assertEqual(set(row.keys()), set(EXPORT_FIELDNAMES))

        by_source = {row["source"]: row for row in data}
        self.assertEqual(by_source["cc"]["title"], "ASUS RTX 5070")
        self.assertEqual(by_source["cc"]["price"], 799.99)
        self.assertEqual(by_source["cc"]["instock"], 1)
        self.assertEqual(by_source["cc"]["category"], "GPUs")
        self.assertEqual(by_source["cc"]["search_term"], "rtx 5070")
        self.assertTrue(by_source["cc"]["timestamp"])
        self.assertEqual(by_source["f2f"]["instock"], 0)
        self.assertIsNone(by_source["f2f"]["price"])

    def test_csv_export_empty_item(self):
        response = self.client.get(reverse("export_item_csv", args=[self.empty_item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        lines = response.content.decode("utf-8").splitlines()
        # Header only, no data rows.
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split(","), EXPORT_FIELDNAMES)

    def test_json_export_empty_item(self):
        response = self.client.get(reverse("export_item_json", args=[self.empty_item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(response.content), [])

    def test_export_missing_item_404(self):
        response = self.client.get(reverse("export_item_csv", args=[999999]))
        self.assertEqual(response.status_code, 404)
