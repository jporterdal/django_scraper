from django.test import SimpleTestCase, TestCase
from django.conf import settings
import json

class F2FInvestigationTests(SimpleTestCase):
    """Phase 2 Step 1 — fixture smoke tests for F2F investigation artifacts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "f2f"

    def test_f2f_html_fixture_exists(self):
        path = self.fixtures / "search_results_sample.html"
        self.assertTrue(path.exists())
        self.assertGreater(len(path.read_text()), 500)

    def test_f2f_json_fixture_exists_and_has_hits(self):
        path = self.fixtures / "search_results_sample.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        hits = data["hits"]["hits"]
        self.assertGreaterEqual(len(hits), 1)
        src = hits[0]["_source"]
        self.assertIn("title", src)
        self.assertIn("variants", src)
        self.assertGreater(len(src["variants"]), 0)

    def test_f2f_investigation_doc_exists(self):
        path = settings.BASE_DIR / "tracking" / "docs" / "f2f_investigation.md"
        self.assertTrue(path.exists())
        text = path.read_text()
        self.assertIn("{term}", text)
        self.assertIn("prod-indexer", text)

class WTInvestigationTests(SimpleTestCase):
    """Phase 2 Step 1 — fixture smoke tests for WT investigation artifacts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "wt"

    def test_wt_html_fixture_exists(self):
        path = self.fixtures / "search_results_sample.html"
        self.assertTrue(path.exists())
        self.assertGreater(len(path.read_text()), 500)

    def test_wt_json_fixture_exists_and_has_results(self):
        path = self.fixtures / "search_results_sample.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        results = data["data"]["results"]
        self.assertGreaterEqual(len(results), 1)
        first = results[0]
        self.assertIn("title", first)
        self.assertIn("price", first)
        self.assertIn("in_stock", first)

    def test_wt_investigation_doc_exists(self):
        path = settings.BASE_DIR / "tracking" / "docs" / "wt_investigation.md"
        self.assertTrue(path.exists())
        text = path.read_text()
        self.assertIn("{term}", text)
        self.assertIn("app-filters.wizardtower.com", text)

class HFXInvestigationTests(SimpleTestCase):
    """Phase 2 Step 1 — fixture smoke tests for HFX investigation artifacts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "hfx"

    def test_hfx_html_fixture_exists(self):
        path = self.fixtures / "search_results_sample.html"
        self.assertTrue(path.exists())
        self.assertGreater(len(path.read_text()), 500)

    def test_hfx_json_fixture_exists_and_has_products(self):
        path = self.fixtures / "search_results_sample.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        products = data["products"]
        self.assertGreaterEqual(len(products), 1)
        product = products[0]
        self.assertIn("display_name", product)
        self.assertIn("variantInfo", product)
        self.assertGreater(len(product["variantInfo"]), 0)

    def test_hfx_investigation_doc_exists(self):
        path = settings.BASE_DIR / "tracking" / "docs" / "hfx_investigation.md"
        self.assertTrue(path.exists())
        text = path.read_text()
        self.assertIn("{term}", text)
        self.assertIn("storepass", text)
