from django.test import TestCase
from django.urls import reverse

from tracking.tests.factories import make_linked_item, make_user


class LoginGateTests(TestCase):
    def test_unauthenticated_view_terms_redirects_to_login(self):
        response = self.client.get(reverse("view_terms"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('view_terms')}",
            fetch_redirect_response=False,
        )

    def test_unauthenticated_export_csv_redirects_to_login(self):
        _, item, _ = make_linked_item()
        response = self.client.get(reverse("export_item_csv", args=[item.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))

    def test_login_page_returns_200_unauthenticated(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_authenticated_view_terms_returns_200(self):
        user = make_user()
        self.client.force_login(user)
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
