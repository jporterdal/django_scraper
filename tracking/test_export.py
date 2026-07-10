import json

from django.test import Client, TestCase
from django.urls import reverse

from .models import SearchableItem, SearchResult, Source, WebUpdate
from .views import EXPORT_FIELDNAMES


class ExportTests(TestCase):
    """Phase 3 Step 6 — per-item CSV/JSON price-history export endpoints."""

    def setUp(self):
        self.client = Client()
        self.source_cc, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Canada Computers",
                "parser_key": "cc",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.source_f2f, _ = Source.objects.update_or_create(
            key="f2f",
            defaults={
                "name": "Face to Face",
                "parser_key": "shopify",
                "base_search_url": "https://example.com/f2f?q={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="rtx 5070", active=True)
        self.empty_item = SearchableItem.objects.create(text="no results", active=True)

        self.update = WebUpdate.objects.create()

        # Two results for the same update: one per source.
        SearchResult.objects.create(
            title="ASUS RTX 5070",
            search_term="rtx 5070",
            price=799.99,
            category="GPUs",
            instock=1,
            item=self.item,
            update=self.update,
            source=self.source_cc,
        )
        SearchResult.objects.create(
            title="MSI RTX 5070",
            search_term="rtx 5070",
            price=None,
            category="Video Cards",
            instock=0,
            item=self.item,
            update=self.update,
            source=self.source_f2f,
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
