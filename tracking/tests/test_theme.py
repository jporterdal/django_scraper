"""HTTP/template assertions for Bootstrap dark-mode theme markup."""

from django.test import TestCase
from django.urls import reverse

from tracking.tests.base import AuthedClientTestCase


class ThemeMarkupAuthedTests(AuthedClientTestCase):
    """Theme plumbing on authenticated pages that extend base.html."""

    def test_view_terms_default_dark_theme_and_toggle(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('data-bs-theme="dark"', content)
        self.assertIn('id="theme-toggle"', content)
        self.assertIn("pricing-tracker-theme", content)
        self.assertIn("/static/tracking/theme.js", content)

    def test_view_terms_theme_following_navbar(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("bg-body-tertiary", content)
        self.assertNotIn("navbar-dark bg-dark", content)


class ThemeMarkupLoginTests(TestCase):
    """Theme plumbing on the anonymous login page (extends base.html)."""

    def test_login_page_default_dark_theme_and_toggle(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn('data-bs-theme="dark"', content)
        self.assertIn('id="theme-toggle"', content)
        self.assertIn("pricing-tracker-theme", content)
        self.assertIn("bg-body-tertiary", content)
        self.assertNotIn("navbar-dark bg-dark", content)
        self.assertIn("/static/tracking/theme.js", content)
