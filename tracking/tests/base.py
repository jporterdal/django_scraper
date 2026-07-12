"""Shared test base classes (not collected as tests)."""

from django.test import TestCase

from .factories import make_linked_item, make_user


class AuthedClientMixin:
    """Force-login a test user before each request."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = make_user()

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)


class AuthedClientTestCase(AuthedClientMixin, TestCase):
    """TestCase with an authenticated client for view tests."""


class LinkedSourceTestCase(AuthedClientMixin, TestCase):
    """One active item wired to a default ``cc`` source via ItemSource."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source, cls.item, cls.item_source = make_linked_item()
