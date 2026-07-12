from django.test import SimpleTestCase, TestCase

class TitleMatchesRulesTests(SimpleTestCase):
    def test_title_matches_rules_stub(self):
        from tracking.matching import title_matches_rules

        title = "MSI RTX 5070 Gaming X Trio"

        self.assertTrue(title_matches_rules(title, [], []))
        self.assertTrue(title_matches_rules(title, ["MSI.*5070"], []))
        self.assertFalse(title_matches_rules(title, ["ASUS.*5070"], []))
        self.assertFalse(title_matches_rules(title, [], ["MSI.*"]))
        self.assertTrue(title_matches_rules(title, ["MSI.*5070"], ["Gigabyte.*"]))
