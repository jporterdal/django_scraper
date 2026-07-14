"""Unit tests for Bulk Add forms/helper (Step 1) and HTTP integration (Step 3)."""

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from tracking.forms import (
    BULK_ADD_TAG_NONE,
    BulkAddItemsForm,
    ItemSourceFormSet,
    create_items_from_bulk_add,
)
from tracking.models import ItemSource, SearchableItem, Tag
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_cc_source, make_item, make_source


class BulkAddItemsFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tag = Tag.objects.create(name="GPU", color="#3498db")

    def _valid_data(self, **overrides):
        data = {
            "tag": BULK_ADD_TAG_NONE,
            "search_terms": "lightning bolt\ncounterspell",
            "priority": str(SearchableItem.Priority.B),
            "allow_duplicate_text": "",
        }
        data.update(overrides)
        return data

    def test_parse_multiline_ignores_blank_lines(self):
        form = BulkAddItemsForm(
            data=self._valid_data(search_terms="foo\n\n  \nbar\n")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["search_terms"], ["foo", "bar"])

    def test_reject_empty_textarea(self):
        form = BulkAddItemsForm(data=self._valid_data(search_terms=""))
        self.assertFalse(form.is_valid())
        self.assertIn("search_terms", form.errors)

    def test_reject_whitespace_only_textarea(self):
        form = BulkAddItemsForm(data=self._valid_data(search_terms="  \n\t\n  "))
        self.assertFalse(form.is_valid())
        self.assertIn("search_terms", form.errors)

    def test_reject_more_than_200_terms_validation_only(self):
        lines = "\n".join(f"term-{i}" for i in range(201))
        form = BulkAddItemsForm(data=self._valid_data(search_terms=lines))
        self.assertFalse(form.is_valid())
        self.assertIn("search_terms", form.errors)

    def test_reject_in_batch_case_insensitive_duplicates(self):
        form = BulkAddItemsForm(
            data=self._valid_data(search_terms="Foo\nfoo")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("search_terms", form.errors)

    def test_reject_existing_term_when_duplicates_disallowed(self):
        make_item(text="Existing Term")
        form = BulkAddItemsForm(
            data=self._valid_data(search_terms="existing term\nbrand new")
        )
        self.assertFalse(form.is_valid())
        errors = form.non_field_errors()
        self.assertTrue(errors)
        self.assertIn("existing term", str(errors).lower())

    def test_allow_duplicate_text_skips_db_collision_and_saves(self):
        make_item(text="Already Here")
        form = BulkAddItemsForm(
            data=self._valid_data(
                search_terms="Already Here\nBrand New",
                allow_duplicate_text="on",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        terms = form.cleaned_data["search_terms"]
        self.assertEqual(terms, ["Already Here", "Brand New"])

        before = SearchableItem.objects.filter(text="Already Here").count()
        created = create_items_from_bulk_add(
            terms=terms,
            tag=None,
            priority=SearchableItem.Priority.B,
            source_forms=[],
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(
            SearchableItem.objects.filter(text="Already Here").count(),
            before + 1,
        )
        self.assertTrue(SearchableItem.objects.filter(text="Brand New").exists())

    def test_reject_unchosen_tag_placeholder(self):
        form = BulkAddItemsForm(data=self._valid_data(tag=""))
        self.assertFalse(form.is_valid())
        self.assertIn("tag", form.errors)

    def test_accept_no_tag(self):
        form = BulkAddItemsForm(data=self._valid_data(tag=BULK_ADD_TAG_NONE))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["tag"])

    def test_accept_existing_tag(self):
        form = BulkAddItemsForm(data=self._valid_data(tag=str(self.tag.pk)))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tag"], self.tag)

    def test_stores_terms_as_typed(self):
        form = BulkAddItemsForm(
            data=self._valid_data(search_terms="Lightning Bolt\nCounterspell")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["search_terms"],
            ["Lightning Bolt", "Counterspell"],
        )

    def test_reject_term_exceeding_max_length(self):
        too_long = "x" * 126
        form = BulkAddItemsForm(data=self._valid_data(search_terms=too_long))
        self.assertFalse(form.is_valid())
        self.assertIn("search_terms", form.errors)


class ItemSourceFormSetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source_a = make_cc_source(name="Source A")
        cls.source_b = make_source(
            key="f2f",
            name="Source B",
            parser_key="shopify",
            base_search_url="https://example.com/search/{term}",
        )

    def _formset_data(self, rows, *, total=None):
        """Build management + row POST data for ItemSourceFormSet."""
        if total is None:
            total = len(rows)
        data = {
            "form-TOTAL_FORMS": str(total),
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(rows):
            prefix = f"form-{i}-"
            data[prefix + "source"] = row.get("source", "")
            data[prefix + "url_suffix"] = row.get("url_suffix", "")
            data[prefix + "pinned_url"] = row.get("pinned_url", "")
            data[prefix + "title_include_patterns"] = row.get(
                "title_include_patterns", ""
            )
            data[prefix + "title_exclude_patterns"] = row.get(
                "title_exclude_patterns", ""
            )
            if row.get("DELETE"):
                data[prefix + "DELETE"] = "on"
        # Pad empty extras if total > len(rows)
        for i in range(len(rows), total):
            prefix = f"form-{i}-"
            data.setdefault(prefix + "source", "")
            data.setdefault(prefix + "url_suffix", "")
            data.setdefault(prefix + "pinned_url", "")
            data.setdefault(prefix + "title_include_patterns", "")
            data.setdefault(prefix + "title_exclude_patterns", "")
        return data

    def test_reject_invalid_regex_on_row(self):
        formset = ItemSourceFormSet(
            data=self._formset_data(
                [
                    {
                        "source": str(self.source_a.pk),
                        "title_include_patterns": "[[",
                    }
                ]
            )
        )
        self.assertFalse(formset.is_valid())
        self.assertTrue(formset.errors[0].get("title_include_patterns"))

    def test_reject_duplicate_source(self):
        formset = ItemSourceFormSet(
            data=self._formset_data(
                [
                    {"source": str(self.source_a.pk)},
                    {"source": str(self.source_a.pk)},
                ]
            )
        )
        self.assertFalse(formset.is_valid())
        self.assertTrue(formset.non_form_errors())

    def test_empty_rows_allowed(self):
        formset = ItemSourceFormSet(data=self._formset_data([{}], total=1))
        self.assertTrue(formset.is_valid(), formset.errors)

    def test_two_distinct_sources_valid(self):
        formset = ItemSourceFormSet(
            data=self._formset_data(
                [
                    {
                        "source": str(self.source_a.pk),
                        "title_include_patterns": "RTX",
                        "url_suffix": "&x=1",
                    },
                    {
                        "source": str(self.source_b.pk),
                        "title_exclude_patterns": "Foil",
                    },
                ]
            )
        )
        self.assertTrue(formset.is_valid(), formset.errors)


class CreateItemsFromBulkAddTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tag = Tag.objects.create(name="MTG")
        cls.source_a = make_cc_source(name="Source A")
        cls.source_b = make_source(
            key="f2f",
            name="Source B",
            parser_key="shopify",
            base_search_url="https://example.com/search/{term}",
        )

    def _valid_source_forms(self, *, with_sources=True):
        if not with_sources:
            formset = ItemSourceFormSet(
                data={
                    "form-TOTAL_FORMS": "1",
                    "form-INITIAL_FORMS": "0",
                    "form-MIN_NUM_FORMS": "0",
                    "form-MAX_NUM_FORMS": "1000",
                    "form-0-source": "",
                    "form-0-url_suffix": "",
                    "form-0-pinned_url": "",
                    "form-0-title_include_patterns": "",
                    "form-0-title_exclude_patterns": "",
                }
            )
            self.assertTrue(formset.is_valid(), formset.errors)
            return list(formset.forms)

        formset = ItemSourceFormSet(
            data={
                "form-TOTAL_FORMS": "2",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-source": str(self.source_a.pk),
                "form-0-url_suffix": "&sort=price",
                "form-0-pinned_url": "",
                "form-0-title_include_patterns": "NM\nLP",
                "form-0-title_exclude_patterns": "Foil",
                "form-1-source": str(self.source_b.pk),
                "form-1-url_suffix": "",
                "form-1-pinned_url": "https://example.com/pinned",
                "form-1-title_include_patterns": "",
                "form-1-title_exclude_patterns": "",
            }
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        return list(formset.forms)

    def test_save_with_tag_priority_and_sources(self):
        terms = ["Alpha", "Beta", "Gamma"]
        priority = SearchableItem.Priority.S
        source_forms = self._valid_source_forms(with_sources=True)

        created = create_items_from_bulk_add(
            terms=terms,
            tag=self.tag,
            priority=priority,
            source_forms=source_forms,
        )
        self.assertEqual(len(created), 3)
        for item, term in zip(created, terms):
            item.refresh_from_db()
            self.assertEqual(item.text, term)
            self.assertEqual(item.priority, priority)
            self.assertTrue(item.active)
            self.assertEqual(list(item.tags.all()), [self.tag])
            sources = list(
                ItemSource.objects.filter(item=item).order_by("source__key")
            )
            self.assertEqual(len(sources), 2)
            by_key = {s.source.key: s for s in sources}
            self.assertEqual(by_key["cc"].url_suffix, "&sort=price")
            self.assertEqual(by_key["cc"].title_include_patterns, ["NM", "LP"])
            self.assertEqual(by_key["cc"].title_exclude_patterns, ["Foil"])
            self.assertEqual(by_key["f2f"].pinned_url, "https://example.com/pinned")
            self.assertEqual(by_key["f2f"].title_include_patterns, [])

    def test_save_with_no_tag(self):
        created = create_items_from_bulk_add(
            terms=["Solo"],
            tag=None,
            priority=SearchableItem.Priority.A,
            source_forms=self._valid_source_forms(with_sources=False),
        )
        self.assertEqual(len(created), 1)
        item = created[0]
        self.assertEqual(item.priority, SearchableItem.Priority.A)
        self.assertEqual(item.tags.count(), 0)
        self.assertEqual(ItemSource.objects.filter(item=item).count(), 0)

    def test_save_with_zero_source_rows(self):
        created = create_items_from_bulk_add(
            terms=["One", "Two"],
            tag=self.tag,
            priority=SearchableItem.Priority.C,
            source_forms=self._valid_source_forms(with_sources=False),
        )
        self.assertEqual(len(created), 2)
        for item in created:
            self.assertEqual(ItemSource.objects.filter(item=item).count(), 0)
            self.assertIn(self.tag, item.tags.all())


class BulkAddHTTPTests(AuthedClientTestCase):
    """Step 3 — end-to-end GET/POST coverage for bulk add wiring."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tag = Tag.objects.create(name="BulkTag", color="#3498db")
        cls.source_a = make_cc_source(name="Source A")
        cls.source_b = make_source(
            key="f2f",
            name="Source B",
            parser_key="shopify",
            base_search_url="https://example.com/search/{term}",
        )

    def _empty_formset(self, total=1):
        data = {
            "form-TOTAL_FORMS": str(total),
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for i in range(total):
            prefix = f"form-{i}-"
            data[prefix + "source"] = ""
            data[prefix + "url_suffix"] = ""
            data[prefix + "pinned_url"] = ""
            data[prefix + "title_include_patterns"] = ""
            data[prefix + "title_exclude_patterns"] = ""
        return data

    def _formset_rows(self, rows):
        data = {
            "form-TOTAL_FORMS": str(len(rows)),
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(rows):
            prefix = f"form-{i}-"
            data[prefix + "source"] = row.get("source", "")
            data[prefix + "url_suffix"] = row.get("url_suffix", "")
            data[prefix + "pinned_url"] = row.get("pinned_url", "")
            data[prefix + "title_include_patterns"] = row.get(
                "title_include_patterns", ""
            )
            data[prefix + "title_exclude_patterns"] = row.get(
                "title_exclude_patterns", ""
            )
        return data

    def _bulk_post(
        self,
        *,
        tag=None,
        search_terms="Alpha\nBeta",
        priority=None,
        allow_duplicate_text=False,
        formset=None,
    ):
        if tag is None:
            tag = str(self.tag.pk)
        if priority is None:
            priority = str(SearchableItem.Priority.B)
        data = {
            "tag": tag,
            "search_terms": search_terms,
            "priority": priority,
        }
        if allow_duplicate_text:
            data["allow_duplicate_text"] = "on"
        if formset is None:
            formset = self._empty_formset()
        data.update(formset)
        return self.client.post(reverse("bulk_add"), data)

    def test_get_bulk_add_renders_fields(self):
        response = self.client.get(reverse("bulk_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="tag"')
        self.assertContains(response, "--- choose ---")
        self.assertContains(response, 'name="search_terms"')
        self.assertContains(response, 'name="priority"')
        self.assertContains(response, 'name="allow_duplicate_text"')
        self.assertContains(response, 'name="form-TOTAL_FORMS"')
        self.assertContains(response, 'name="form-INITIAL_FORMS"')

    def test_get_with_tag_query_preselects_tag(self):
        response = self.client.get(
            reverse("bulk_add"), {"tag": str(self.tag.pk)}
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial.get("tag"), str(self.tag.pk))
        self.assertContains(
            response,
            f'<option value="{self.tag.pk}" selected',
            html=False,
        )

    def test_post_happy_path_creates_items_tag_and_sources(self):
        before = SearchableItem.objects.count()
        priority = SearchableItem.Priority.S
        terms = ["Happy One", "Happy Two", "Happy Three"]
        response = self._bulk_post(
            search_terms="\n".join(terms),
            priority=str(priority),
            formset=self._formset_rows(
                [
                    {
                        "source": str(self.source_a.pk),
                        "url_suffix": "&sort=price",
                        "title_include_patterns": "NM\nLP",
                        "title_exclude_patterns": "Foil",
                    },
                    {
                        "source": str(self.source_b.pk),
                        "pinned_url": "https://example.com/pinned",
                    },
                ]
            ),
        )
        self.assertRedirects(response, reverse("view_terms"))
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Added 3 items" in m for m in messages))
        self.assertEqual(SearchableItem.objects.count(), before + 3)

        for term in terms:
            item = SearchableItem.objects.get(text=term)
            self.assertEqual(item.priority, priority)
            self.assertEqual(list(item.tags.all()), [self.tag])
            sources = list(
                ItemSource.objects.filter(item=item).order_by("source__key")
            )
            self.assertEqual(len(sources), 2)
            by_key = {s.source.key: s for s in sources}
            self.assertEqual(by_key["cc"].url_suffix, "&sort=price")
            self.assertEqual(by_key["cc"].title_include_patterns, ["NM", "LP"])
            self.assertEqual(by_key["cc"].title_exclude_patterns, ["Foil"])
            self.assertEqual(by_key["f2f"].pinned_url, "https://example.com/pinned")

    def test_post_no_tag_creates_items_without_tags(self):
        before = SearchableItem.objects.count()
        response = self._bulk_post(
            tag=BULK_ADD_TAG_NONE,
            search_terms="NoTag Alpha\nNoTag Beta",
        )
        self.assertRedirects(response, reverse("view_terms"))
        self.assertEqual(SearchableItem.objects.count(), before + 2)
        for text in ("NoTag Alpha", "NoTag Beta"):
            item = SearchableItem.objects.get(text=text)
            self.assertEqual(item.tags.count(), 0)

    def test_post_unchosen_tag_creates_nothing(self):
        before = SearchableItem.objects.count()
        response = self._bulk_post(tag="", search_terms="Should Not Create")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)
        self.assertFalse(
            SearchableItem.objects.filter(text="Should Not Create").exists()
        )

    def test_post_zero_sources_creates_items_without_item_sources(self):
        before_items = SearchableItem.objects.count()
        before_sources = ItemSource.objects.count()
        response = self._bulk_post(
            search_terms="ZeroSrc One\nZeroSrc Two",
            formset=self._empty_formset(total=1),
        )
        self.assertRedirects(response, reverse("view_terms"))
        self.assertEqual(SearchableItem.objects.count(), before_items + 2)
        self.assertEqual(ItemSource.objects.count(), before_sources)
        for text in ("ZeroSrc One", "ZeroSrc Two"):
            item = SearchableItem.objects.get(text=text)
            self.assertEqual(ItemSource.objects.filter(item=item).count(), 0)

    def test_post_in_batch_duplicate_creates_nothing(self):
        before = SearchableItem.objects.count()
        response = self._bulk_post(search_terms="DupTerm\ndupterm")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)

    def test_post_existing_db_term_disallowed_creates_nothing(self):
        make_item(text="Existing Collision")
        before = SearchableItem.objects.count()
        response = self._bulk_post(
            search_terms="existing collision\nBrand New Term",
            allow_duplicate_text=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)
        self.assertContains(response, "already exist")
        self.assertFalse(
            SearchableItem.objects.filter(text="Brand New Term").exists()
        )

    def test_post_allow_duplicate_text_creates_one_per_term(self):
        make_item(text="Already Here")
        before_dup = SearchableItem.objects.filter(text="Already Here").count()
        before_total = SearchableItem.objects.count()
        response = self._bulk_post(
            search_terms="Already Here\nFresh Term",
            allow_duplicate_text=True,
        )
        self.assertRedirects(response, reverse("view_terms"))
        self.assertEqual(SearchableItem.objects.count(), before_total + 2)
        self.assertEqual(
            SearchableItem.objects.filter(text="Already Here").count(),
            before_dup + 1,
        )
        self.assertTrue(SearchableItem.objects.filter(text="Fresh Term").exists())

    def test_post_over_200_terms_validation_only(self):
        before = SearchableItem.objects.count()
        lines = "\n".join(f"cap-term-{i}" for i in range(201))
        response = self._bulk_post(search_terms=lines)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)

    def test_post_invalid_regex_creates_nothing(self):
        before = SearchableItem.objects.count()
        response = self._bulk_post(
            search_terms="Regex Fail",
            formset=self._formset_rows(
                [
                    {
                        "source": str(self.source_a.pk),
                        "title_include_patterns": "[[",
                    }
                ]
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)
        self.assertFalse(SearchableItem.objects.filter(text="Regex Fail").exists())

    def test_post_duplicate_source_creates_nothing(self):
        before = SearchableItem.objects.count()
        response = self._bulk_post(
            search_terms="Dup Source Fail",
            formset=self._formset_rows(
                [
                    {"source": str(self.source_a.pk)},
                    {"source": str(self.source_a.pk)},
                ]
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)

    def test_post_empty_terms_creates_nothing(self):
        before = SearchableItem.objects.count()
        response = self._bulk_post(search_terms="  \n\t\n  ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SearchableItem.objects.count(), before)

    def test_add_tag_with_next_redirects_to_bulk_add_with_tag(self):
        bulk_url = reverse("bulk_add")
        response = self.client.post(
            f"{reverse('add_tag')}?next={bulk_url}",
            {"name": "FromBulk", "color": "#9b59b6", "next": bulk_url},
        )
        new_tag = Tag.objects.get(name="FromBulk")
        self.assertRedirects(response, f"{bulk_url}?tag={new_tag.pk}")
        follow = self.client.get(response.url)
        self.assertEqual(follow.status_code, 200)
        self.assertEqual(
            follow.context["form"].initial.get("tag"), str(new_tag.pk)
        )

    def test_add_tag_cancel_with_next_returns_to_bulk_add(self):
        bulk_url = reverse("bulk_add")
        response = self.client.get(f"{reverse('add_tag')}?next={bulk_url}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["back_url"], bulk_url)
        self.assertContains(response, bulk_url)

    def test_add_tag_rejects_open_redirect_next(self):
        response = self.client.get(
            reverse("add_tag"),
            {"next": "https://evil.example/phish"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next_param"], "")
        self.assertEqual(response.context["back_url"], reverse("view_tags"))

        post = self.client.post(
            reverse("add_tag"),
            {
                "name": "SafeRedirect",
                "color": "#111111",
                "next": "https://evil.example/phish",
            },
        )
        self.assertRedirects(post, reverse("view_tags"))
        self.assertTrue(Tag.objects.filter(name="SafeRedirect").exists())

    def test_unauthenticated_bulk_add_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("bulk_add"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('bulk_add')}",
            fetch_redirect_response=False,
        )
