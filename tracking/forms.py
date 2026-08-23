import json
import re
from collections import defaultdict

from django import forms
from django.db import transaction
from django.db.models.functions import Lower
from django.forms import BaseFormSet, formset_factory

from .metadata import request_metadata_refresh, sync_metadata_after_save
from .metadata_providers import PROVIDERS as metadata_provider_registry
from .models import (
    ItemSource,
    SearchableItem,
    Source,
    Tag,
    UpdateSchedule,
    observed_values_for_item,
    vendor_scoped_suggestions_for_items,
)
from .parsers import sources as parser_registry
from .ratelimit import PROFILE_CHOICES as rate_limit_profile_choices

# Sentinels for BulkEditItemsForm's per-field tri-state-or-wider choices —
# distinct from every field's normal blank/false/empty value, so a partial
# bulk-edit round only touches fields the operator explicitly set (see
# design.md Decision 3). BULK_EDIT_CLEAR is its own sentinel (not "") because
# a plain ChoiceField can't otherwise distinguish "explicitly selected blank"
# from "field absent from this POST" — both collapse to "" in cleaned_data.
BULK_EDIT_LEAVE = "__leave__"
BULK_EDIT_CLEAR = "__clear__"

# Sentinel for BulkAddItemsForm "No tag" choice (distinct from unchosen "").
BULK_ADD_TAG_NONE = "__none__"
BULK_ADD_MAX_TERMS = 200
BULK_ADD_TERM_MAX_LENGTH = 125


BASE_SEARCH_URL_HELP_TEXT = (
    "Search URL or API endpoint. For GET sources, include {term} where the "
    "URL-encoded query belongs (e.g. https://example.com/search?q={term}). "
    "For POST sources, {term} is optional — the query usually lives in the "
    "request body template instead."
)

HTTP_METHOD_HELP_TEXT = (
    "GET sends the search term in the URL; POST sends it in the JSON body "
    "template below."
)

REQUEST_BODY_TEMPLATE_HELP_TEXT = (
    "JSON object used as the POST request body. Put {term} in string values "
    "for the search query (plain text, not URL-encoded). Leave empty or {} "
    "for GET sources."
)

REQUEST_HEADERS_HELP_TEXT = (
    "JSON object of extra HTTP headers (e.g. Accept, Origin, Referer). Use "
    "{term} in string values where a header should reflect the current search "
    "query — it is URL-encoded the same way as in GET search URLs (e.g. "
    "Referer: https://example.com/search?q={term})."
)

MAX_PAGES_HELP_TEXT = (
    "Maximum search result pages to fetch per item-source (1 = single page). "
    "POST sources always fetch a single page regardless of this setting."
)


INCLUDE_HELP_TEXT = (
    "One regex per line. A result is kept if it matches at least one include "
    "pattern (leave empty to allow all). Patterns are case-insensitive and match "
    "anywhere in the title. "
    "Examples — include: Lightning Bolt · \\(NM\\) · MSI.*5070 · RTX 5070"
)

EXCLUDE_HELP_TEXT = (
    "One regex per line. A result is dropped if it matches any exclude pattern "
    "(excludes win over includes). Plain words match as substrings (Foil matches "
    "[Foil]); use \\[Beatdown\\] for a literal bracket and \\b...\\b for word "
    "boundaries. "
    "Examples — exclude: Foil · Japanese · \\bJP\\b · \\bTi\\b · Refurb"
)

EXPECTED_PRODUCT_LINE_HELP_TEXT = (
    "One row per distinct value, labeled by vendor or 'Manual entry'. Check "
    "any that apply; uncheck to remove. A checked vendor row disambiguates "
    "only that vendor's results; a 'Manual entry' row applies to every "
    "vendor configured for this item. Leave everything unchecked/empty to "
    "skip this check."
)

EXPECTED_PRODUCT_LINE_MANUAL_HELP_TEXT = (
    "One new value per line, for anything not covered by a row above (e.g. "
    "a different vendor's own wording for the same product line). Added as "
    "'Manual entry' rows, applying to every vendor configured for this item."
)

EXPECTED_CATEGORY_HELP_TEXT = (
    "One row per distinct value, labeled by vendor or 'Manual entry'. Check "
    "any that apply; uncheck to remove. A checked vendor row narrows only "
    "that vendor's results; a 'Manual entry' row applies to every vendor "
    "configured for this item. Independent of expected product line. Leave "
    "everything unchecked/empty to skip this check."
)

EXPECTED_CATEGORY_MANUAL_HELP_TEXT = (
    "One new value per line, for anything not covered by a row above. "
    "Added as 'Manual entry' rows, applying to every vendor configured for "
    "this item."
)

PINNED_URL_HELP_TEXT = (
    "When set, the scraper fetches this URL directly with GET instead of building "
    "a search URL from the source template. Use for stubborn listings where search "
    "does not return the right result. The source's parser must be able to parse "
    "the pinned endpoint's response."
)

METADATA_PROVIDER_HELP_TEXT = (
    "Optional external metadata provider (e.g. Scryfall) to enrich this item "
    "with a thumbnail, description, and link. Leave as None to disable. "
    "Changing this resets any previously fetched metadata and re-fetches."
)


def _lines_to_list(raw):
    """Split textarea input into a list of patterns, dropping blank lines."""
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _list_to_lines(value):
    """Join a stored JSON list into newline-separated text for display."""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(p) for p in value)
    return value or ""


def _apply_bootstrap_form_classes(form):
    """Add Bootstrap widget classes to every field on a form.

    ``CheckboxSelectMultiple``/``RadioSelect`` are skipped: Django applies a
    widget's ``attrs["class"]`` to both the outer options container *and*
    each individual `<input>`, so forcing "form-check-input" here would also
    land on the container div and shrink it to Bootstrap's 1em checkbox
    dimensions. Left unstyled; their containing markup supplies structure
    instead (see ``searchableitem_form.html``'s scrollable wrapper).
    """
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
            continue
        if isinstance(widget, forms.CheckboxInput):
            css = "form-check-input"
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            css = "form-select"
        else:
            css = "form-control"
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = (existing + " " + css).strip()


def _suggestion_choice_value(source_key, value):
    """Encode a suggestion/stored-entry checkbox as a single form value.

    A checkbox's identity is the exact ``(source, value)`` pair it represents
    (``source=None`` for a manually-entered value), not the bare value alone —
    see expected-value-vendor-provenance design.md Decision 3. Reuses the
    ``json.dumps([source_key, value])`` encoding bulk-item-editing's own
    vendor-scoped suggestion checkboxes already established, rather than
    inventing a second scheme.
    """
    return json.dumps([source_key, value])


def _parse_suggestion_choice_value(raw):
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list) or len(parsed) != 2:
        return None
    return parsed[0], parsed[1]


def _suggestion_choices(instance, field_name):
    """One checkbox choice per (source, value) pair to display for ``field_name``.

    Includes every vendor-observed pair scoped to the item's configured
    sources, plus any pair already stored on the instance that isn't among
    those observations — a vendor-tagged entry whose vendor no longer
    reports that value, or a manually-entered (``source: None``) entry —
    so no stored entry is ever silently dropped from the form (design.md
    Decision 4). Not deduplicated across vendors: a value shared by two
    vendors still renders as two distinct, independently checkable rows.
    Grouped by vendor key, alphabetically within each group; manual
    ("Manual entry") rows sort last.
    """
    pairs = set()
    if instance is not None and instance.pk:
        pairs.update(observed_values_for_item(instance, field_name))
        pairs.update(
            (entry["source"], entry["value"])
            for entry in getattr(instance, f"expected_{field_name}")
        )

    def sort_key(pair):
        source_key, value = pair
        return (source_key is None, (source_key or "").lower(), value.lower())

    ordered = sorted(pairs, key=sort_key)
    return [
        (
            _suggestion_choice_value(source_key, value),
            f"{value} (Manual entry)" if source_key is None else f"{value} ({source_key})",
        )
        for source_key, value in ordered
    ]


def _merge_checked_and_manual(checked_choice_values, manual_text):
    """Combine checked (source, value) checkboxes with newly added manual lines.

    Every checked box already carries its own exact vendor (or ``None`` for
    a "Manual entry" row); every manual textarea line becomes a new
    ``source: None`` entry. Deduplicates by exact ``(value, source)`` pair
    equality, preserving first-occurrence order — not by value alone (design.md
    Decision 5).
    """
    pairs = []
    for raw in checked_choice_values or []:
        parsed = _parse_suggestion_choice_value(raw)
        if parsed is not None:
            pairs.append(tuple(parsed))
    pairs.extend((None, value) for value in _lines_to_list(manual_text))

    seen = set()
    result = []
    for source_key, value in pairs:
        key = (source_key, value)
        if key in seen:
            continue
        seen.add(key)
        result.append({"value": value, "source": source_key})
    return result


class SearchableItemForm(forms.ModelForm):
    """Form for editing a SearchableItem (search term, priority, active, tags).

    ``expected_product_line``/``expected_category`` are list-valued model
    fields (each entry a ``{"value", "source"}`` pair), each backed here by
    two form fields: a checkbox group with one row per distinct ``(source,
    value)`` pair — vendor-labeled suggestions from ``ObservedCategoryValue``
    plus every already-stored pair, so a stale vendor entry or an existing
    "Manual entry" row is never silently dropped — and a manual free-text
    textarea used only to add brand-new values (always stored with
    ``source: None``). Neither model field is bound directly; both are
    assembled from the two form fields in ``save()``.
    """

    expected_product_line_suggestions = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Expected product line",
        help_text=EXPECTED_PRODUCT_LINE_HELP_TEXT,
    )
    expected_product_line_manual = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Add expected product line value(s)",
        help_text=EXPECTED_PRODUCT_LINE_MANUAL_HELP_TEXT,
    )
    expected_category_suggestions = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Expected category",
        help_text=EXPECTED_CATEGORY_HELP_TEXT,
    )
    expected_category_manual = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Add expected category value(s)",
        help_text=EXPECTED_CATEGORY_MANUAL_HELP_TEXT,
    )
    metadata_provider_key = forms.ChoiceField(
        required=False,
        label="Metadata provider",
        help_text=METADATA_PROVIDER_HELP_TEXT,
    )

    class Meta:
        model = SearchableItem
        fields = ["text", "priority", "active", "tags", "metadata_provider_key"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_form_classes(self)

        self.fields["metadata_provider_key"].choices = [
            ("", "None"),
            *((key, key) for key in metadata_provider_registry),
        ]

        instance = getattr(self, "instance", None)
        self.fields["expected_product_line_suggestions"].choices = _suggestion_choices(
            instance, "product_line"
        )
        self.fields["expected_category_suggestions"].choices = _suggestion_choices(
            instance, "category"
        )

        if instance is not None and instance.pk:
            self.initial["expected_product_line_suggestions"] = [
                _suggestion_choice_value(entry["source"], entry["value"])
                for entry in instance.expected_product_line
            ]
            self.initial["expected_category_suggestions"] = [
                _suggestion_choice_value(entry["source"], entry["value"])
                for entry in instance.expected_category
            ]

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.expected_product_line = _merge_checked_and_manual(
            self.cleaned_data.get("expected_product_line_suggestions"),
            self.cleaned_data.get("expected_product_line_manual"),
        )
        instance.expected_category = _merge_checked_and_manual(
            self.cleaned_data.get("expected_category_suggestions"),
            self.cleaned_data.get("expected_category_manual"),
        )
        provider_changed = "metadata_provider_key" in self.changed_data
        text_changed = "text" in self.changed_data
        if commit:
            instance.save()
            self.save_m2m()
            sync_metadata_after_save(
                instance, provider_changed=provider_changed, text_changed=text_changed
            )
        return instance


class SearchableItemCreateForm(SearchableItemForm):
    """Create view: search text plus the optional expected product-line/category."""

    class Meta(SearchableItemForm.Meta):
        fields = ["text", "metadata_provider_key"]


class ItemSourceForm(forms.ModelForm):
    """Form for editing an ItemSource, including include/exclude title patterns.

    The pattern fields are backed by JSONField lists on the model, but presented
    here as textareas (one regex per line). Conversion between the newline text
    and the JSON list happens in __init__ (list -> text) and clean_* (text -> list).
    """

    title_include_patterns = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        help_text=INCLUDE_HELP_TEXT,
        label="Include patterns",
    )
    title_exclude_patterns = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        help_text=EXCLUDE_HELP_TEXT,
        label="Exclude patterns",
    )

    class Meta:
        model = ItemSource
        fields = [
            "source",
            "url_suffix",
            "pinned_url",
            "title_include_patterns",
            "title_exclude_patterns",
        ]
        help_texts = {
            "pinned_url": PINNED_URL_HELP_TEXT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_form_classes(self)

        # ModelForm pre-populates self.initial from the instance with the raw
        # JSON lists; convert those to newline-joined text for the textareas.
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk:
            self.initial["title_include_patterns"] = _list_to_lines(
                instance.title_include_patterns
            )
            self.initial["title_exclude_patterns"] = _list_to_lines(
                instance.title_exclude_patterns
            )

    def _clean_patterns(self, field_name):
        patterns = _lines_to_list(self.cleaned_data.get(field_name))
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise forms.ValidationError(
                    f"Invalid regex pattern {pattern!r}: {exc}"
                )
        return patterns

    def clean_title_include_patterns(self):
        return self._clean_patterns("title_include_patterns")

    def clean_title_exclude_patterns(self):
        return self._clean_patterns("title_exclude_patterns")


class SourceForm(forms.ModelForm):
    """Form for creating/editing a Source, including the parser selector.

    ``parser_key`` is a dropdown populated from the parser registry so users
    can only pick a registered parser. On the edit form ``key`` (the primary
    key) is disabled since it can't change once rows reference it.
    """

    parser_key = forms.ChoiceField(
        choices=[(k, k) for k in parser_registry],
        label="Parser",
        help_text="Which registered parser handles responses from this source.",
    )

    rate_limit_profile = forms.ChoiceField(
        choices=rate_limit_profile_choices,
        required=False,
        label="Rate-limit profile",
        help_text=(
            "How this source's rate-limit budget is extracted and paced. "
            "Leave as 'None' for fixed-delay pacing (the default for HTML sources)."
        ),
    )

    class Meta:
        model = Source
        fields = [
            "key",
            "name",
            "parser_key",
            "rate_limit_profile",
            "http_method",
            "base_search_url",
            "request_body_template",
            "request_headers",
            "page_size",
            "max_pages",
        ]
        widgets = {
            "request_body_template": forms.Textarea(attrs={"rows": 10}),
            "request_headers": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "base_search_url": BASE_SEARCH_URL_HELP_TEXT,
            "http_method": HTTP_METHOD_HELP_TEXT,
            "request_body_template": REQUEST_BODY_TEMPLATE_HELP_TEXT,
            "request_headers": REQUEST_HEADERS_HELP_TEXT,
            "max_pages": MAX_PAGES_HELP_TEXT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_form_classes(self)

        for name in ("request_body_template", "request_headers"):
            widget = self.fields[name].widget
            widget.attrs["class"] = (widget.attrs.get("class", "") + " font-monospace").strip()

        self.fields["page_size"].required = False
        self.fields["max_pages"].required = False

        # key is the primary key: allow it on create, lock it on edit.
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk:
            self.fields["key"].disabled = True
            self.fields["key"].help_text = "The key cannot be changed once created."

    def clean(self):
        cleaned = super().clean()
        if cleaned is None:
            return cleaned

        url = cleaned.get("base_search_url", "")
        http_method = cleaned.get("http_method", Source.HttpMethod.GET)
        if http_method != Source.HttpMethod.POST and "{term}" not in url:
            self.add_error(
                "base_search_url",
                "The search URL must contain '{term}' so the query can be inserted.",
            )
        return cleaned


ANCHOR_TIME_HELP_TEXT = (
    "Reference time-of-day (America/Halifax). Daily runs once at this time; "
    "Twice Daily runs at this time and again 12 hours later; Hourly uses only "
    "the minute — that many minutes past each hour."
)


class UpdateScheduleForm(forms.ModelForm):
    """Form for creating/editing an ``UpdateSchedule``.

    ``frequency`` is rendered as a plain ``<select>`` over the preset
    ``Frequency`` choices ("Hourly" / "Twice Daily" / "Daily") — the default
    widget for a ``TextChoices`` field. There is deliberately no free-form
    cron/interval field; the cadence is always one of the presets.
    """

    class Meta:
        model = UpdateSchedule
        fields = ["name", "frequency", "anchor_time", "tag", "enabled"]
        widgets = {
            "anchor_time": forms.TimeInput(
                attrs={"type": "time"}, format="%H:%M"
            ),
        }
        help_texts = {
            "anchor_time": ANCHOR_TIME_HELP_TEXT,
            "frequency": "How often this scrape runs.",
            "tag": "Limit runs to active items with this tag. Leave blank for all active items.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # "All active items" reads better than the default empty label for the
        # optional tag scope.
        self.fields["tag"].empty_label = "All active items"
        _apply_bootstrap_form_classes(self)


class BulkAddItemsForm(forms.Form):
    """Form for creating many SearchableItems from a multiline term list."""

    tag = forms.ChoiceField(
        choices=[],  # populated in __init__
        required=True,
        label="Tag",
    )
    search_terms = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "rows": 12,
                "title": f"Maximum {BULK_ADD_MAX_TERMS} search terms per submission",
            }
        ),
        help_text=(
            f"One search term per line. Maximum {BULK_ADD_MAX_TERMS} terms."
        ),
        label="Search terms",
    )
    priority = forms.TypedChoiceField(
        choices=SearchableItem.Priority.choices,
        coerce=int,
        initial=SearchableItem.Priority.B,
        label="Priority",
    )
    allow_duplicate_text = forms.BooleanField(
        required=False,
        initial=False,
        label="Add terms even if entries exist with identical text (leave unchecked to verify no duplication of text)",
    )
    metadata_provider_key = forms.ChoiceField(
        required=False,
        label="Metadata provider",
        help_text=METADATA_PROVIDER_HELP_TEXT,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tag_choices = [
            ("", "--- Choice Required ---"),
            (BULK_ADD_TAG_NONE, "No tag"),
        ]
        tag_choices.extend(
            (str(pk), name)
            for pk, name in Tag.objects.order_by("name").values_list("pk", "name")
        )
        self.fields["tag"].choices = tag_choices
        self.fields["metadata_provider_key"].choices = [
            ("", "None"),
            *((key, key) for key in metadata_provider_registry),
        ]
        _apply_bootstrap_form_classes(self)

    def clean_tag(self):
        value = self.cleaned_data["tag"]
        if value == BULK_ADD_TAG_NONE:
            return None
        try:
            return Tag.objects.get(pk=value)
        except (Tag.DoesNotExist, ValueError, TypeError):
            raise forms.ValidationError("Select a valid tag or No tag.")

    def clean_search_terms(self):
        raw = self.cleaned_data.get("search_terms") or ""
        terms = [line.strip() for line in raw.splitlines() if line.strip()]
        if not terms:
            raise forms.ValidationError("Enter at least one search term.")
        if len(terms) > BULK_ADD_MAX_TERMS:
            raise forms.ValidationError(
                f"At most {BULK_ADD_MAX_TERMS} search terms are allowed."
            )
        for term in terms:
            if len(term) > BULK_ADD_TERM_MAX_LENGTH:
                raise forms.ValidationError(
                    f"Each term must be at most {BULK_ADD_TERM_MAX_LENGTH} "
                    f"characters (too long: {term[:40]!r}…)."
                )
        seen_lower = set()
        for term in terms:
            key = term.casefold()
            if key in seen_lower:
                raise forms.ValidationError(
                    f"Duplicate term in the list (case-insensitive): {term!r}."
                )
            seen_lower.add(key)
        return terms

    def clean(self):
        cleaned = super().clean()
        if cleaned is None:
            return cleaned

        terms = cleaned.get("search_terms")
        allow_duplicate_text = cleaned.get("allow_duplicate_text")
        if terms and not allow_duplicate_text:
            term_by_lower = {}
            for term in terms:
                term_by_lower.setdefault(term.lower(), term)
            existing_lowers = set(
                SearchableItem.objects.annotate(text_lower=Lower("text"))
                .filter(text_lower__in=list(term_by_lower.keys()))
                .values_list("text_lower", flat=True)
            )
            collisions = [
                term_by_lower[low]
                for low in term_by_lower
                if low in existing_lowers
            ]
            if collisions:
                listed = ", ".join(repr(t) for t in collisions)
                raise forms.ValidationError(
                    f"The following terms already exist: {listed}."
                )
        return cleaned


class BaseItemSourceFormSet(BaseFormSet):
    """Formset of ItemSourceForm rows; sources must be unique among filled rows."""

    def clean(self):
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue
            if not form.cleaned_data:
                continue
            source = form.cleaned_data.get("source")
            if not source:
                continue
            source_id = source.pk
            if source_id in seen:
                raise forms.ValidationError(
                    "Each source may only be selected once."
                )
            seen.add(source_id)


ItemSourceFormSet = formset_factory(
    ItemSourceForm,
    formset=BaseItemSourceFormSet,
    extra=1,
    can_delete=True,
)


def create_items_from_bulk_add(terms, tag, priority, source_forms, metadata_provider_key=""):
    """Atomically create SearchableItems (and optional ItemSources) from bulk add.

    ``source_forms`` should be validated ``ItemSourceForm`` instances. Empty or
    deleted rows are skipped. A non-blank ``metadata_provider_key`` is applied
    to every created item and requests a metadata refresh for each (task 3.4).
    Returns the list of created ``SearchableItem``s.
    """
    created = []
    with transaction.atomic():
        for term in terms:
            item = SearchableItem.objects.create(
                text=term, priority=priority, metadata_provider_key=metadata_provider_key
            )
            if tag is not None:
                item.tags.add(tag)
            for form in source_forms:
                data = getattr(form, "cleaned_data", None) or {}
                if data.get("DELETE"):
                    continue
                source = data.get("source")
                if not source:
                    continue
                ItemSource.objects.create(
                    item=item,
                    source=source,
                    url_suffix=data.get("url_suffix") or "",
                    pinned_url=data.get("pinned_url") or "",
                    title_include_patterns=data.get("title_include_patterns") or [],
                    title_exclude_patterns=data.get("title_exclude_patterns") or [],
                )
            if metadata_provider_key:
                request_metadata_refresh(item)
            created.append(item)
    return created


def _bulk_edit_suggestion_choice_value(source_key, value):
    """Encode a vendor-scoped suggestion choice as a single form value.

    Bulk edit needs to know which vendor a checked suggestion came from (to
    resolve the correct item subset at apply time), unlike the single-item
    form's suggestion checkboxes, which only ever reconcile against one
    item's own list and so can get away with a bare value string.
    """
    return json.dumps([source_key, value])


def _parse_bulk_edit_suggestion_choice_value(raw):
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list) or len(parsed) != 2:
        return None
    return parsed[0], parsed[1]


class BulkEditItemsForm(forms.Form):
    """Combined bulk-edit form for the workspace; every field defaults to
    "leave unchanged" (see ``BULK_EDIT_LEAVE``), so an apply round only
    touches the fields the operator explicitly set away from that default.
    """

    priority = forms.ChoiceField(choices=[], required=False, label="Priority")
    active = forms.ChoiceField(
        choices=[
            (BULK_EDIT_LEAVE, "Leave unchanged"),
            ("activate", "Activate"),
            ("deactivate", "Deactivate"),
        ],
        required=False,
        label="Active",
    )
    tags_add = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        label="Add tags",
    )
    tags_remove = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        label="Remove tags",
    )
    metadata_provider_key = forms.ChoiceField(
        choices=[],
        required=False,
        label="Metadata provider",
        help_text=METADATA_PROVIDER_HELP_TEXT,
    )
    expected_product_line_suggestions = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Add expected product line",
        help_text=(
            "Checking a suggestion adds it only to selected items that have "
            "that vendor configured; other items in the selection are unaffected."
        ),
    )
    expected_category_suggestions = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Add expected category",
        help_text=(
            "Checking a suggestion adds it only to selected items that have "
            "that vendor configured; other items in the selection are unaffected."
        ),
    )

    def __init__(self, *args, item_ids=None, **kwargs):
        self.item_ids = list(item_ids or [])
        super().__init__(*args, **kwargs)

        self.fields["priority"].choices = [
            (BULK_EDIT_LEAVE, "Leave unchanged"),
            *((str(value), label) for value, label in SearchableItem.Priority.choices),
        ]
        self.fields["metadata_provider_key"].choices = [
            (BULK_EDIT_LEAVE, "Leave unchanged"),
            (BULK_EDIT_CLEAR, "Clear (no provider)"),
            *((key, key) for key in metadata_provider_registry),
        ]
        self.initial.setdefault("priority", BULK_EDIT_LEAVE)
        self.initial.setdefault("active", BULK_EDIT_LEAVE)
        self.initial.setdefault("metadata_provider_key", BULK_EDIT_LEAVE)

        self.product_line_choice_groups = self._build_choice_groups(
            vendor_scoped_suggestions_for_items(self.item_ids, "product_line")
        )
        self.category_choice_groups = self._build_choice_groups(
            vendor_scoped_suggestions_for_items(self.item_ids, "category")
        )
        self.fields["expected_product_line_suggestions"].choices = [
            choice
            for group in self.product_line_choice_groups
            for choice in group["choices"]
        ]
        self.fields["expected_category_suggestions"].choices = [
            choice
            for group in self.category_choice_groups
            for choice in group["choices"]
        ]

        _apply_bootstrap_form_classes(self)

    @staticmethod
    def _build_choice_groups(groups):
        return [
            {
                "source": group["source"],
                "item_count": group["item_count"],
                "choices": [
                    (
                        _bulk_edit_suggestion_choice_value(group["source"].key, value),
                        value,
                    )
                    for value in group["values"]
                ],
            }
            for group in groups
        ]

    def clean_priority(self):
        value = self.cleaned_data.get("priority")
        if not value or value == BULK_EDIT_LEAVE:
            return None
        return int(value)

    def clean_active(self):
        value = self.cleaned_data.get("active")
        if value == "activate":
            return True
        if value == "deactivate":
            return False
        return None

    def clean_metadata_provider_key(self):
        value = self.cleaned_data.get("metadata_provider_key")
        if not value or value == BULK_EDIT_LEAVE:
            return BULK_EDIT_LEAVE
        if value == BULK_EDIT_CLEAR:
            return ""
        return value

    def clean_expected_product_line_suggestions(self):
        return self._parse_suggestions(
            self.cleaned_data.get("expected_product_line_suggestions")
        )

    def clean_expected_category_suggestions(self):
        return self._parse_suggestions(
            self.cleaned_data.get("expected_category_suggestions")
        )

    @staticmethod
    def _parse_suggestions(raw_values):
        parsed = []
        for raw in raw_values or []:
            pair = _parse_bulk_edit_suggestion_choice_value(raw)
            if pair is not None:
                parsed.append(pair)
        return parsed


def _resolve_suggestion_subsets(item_ids, suggestions):
    """Map item pk -> list of values to add, for vendor-scoped suggestions.

    ``suggestions`` is a list of ``(source_key, value)`` pairs. Batched to one
    query per distinct vendor among the checked suggestions — not one query
    per item (see design.md's query-count requirement).
    """
    result = defaultdict(list)
    if not suggestions:
        return result

    values_by_vendor = defaultdict(list)
    for source_key, value in suggestions:
        values_by_vendor[source_key].append(value)

    for source_key, values in values_by_vendor.items():
        qualifying_item_ids = ItemSource.objects.filter(
            item_id__in=item_ids, source_id=source_key
        ).values_list("item_id", flat=True)
        for item_id in qualifying_item_ids:
            result[item_id].extend(values)
    return result


def _apply_bulk_edit_to_item(
    item,
    *,
    priority,
    active,
    tags_add,
    tags_remove,
    metadata_provider_key,
    expected_product_line_add,
    expected_category_add,
):
    update_fields = []
    if priority is not None:
        item.priority = priority
        update_fields.append("priority")
    if active is not None:
        item.active = active
        update_fields.append("active")
    if expected_product_line_add:
        item.expected_product_line = list(
            dict.fromkeys([*item.expected_product_line, *expected_product_line_add])
        )
        update_fields.append("expected_product_line")
    if expected_category_add:
        item.expected_category = list(
            dict.fromkeys([*item.expected_category, *expected_category_add])
        )
        update_fields.append("expected_category")

    provider_changed = (
        metadata_provider_key != BULK_EDIT_LEAVE
        and item.metadata_provider_key != metadata_provider_key
    )
    if provider_changed:
        item.metadata_provider_key = metadata_provider_key
        update_fields.append("metadata_provider_key")

    if update_fields:
        item.save(update_fields=update_fields)

    if tags_add:
        item.tags.add(*tags_add)
    if tags_remove:
        item.tags.remove(*tags_remove)

    if provider_changed:
        sync_metadata_after_save(item, provider_changed=True, text_changed=False)


def apply_bulk_edit(item_ids, cleaned_data):
    """Best-effort per-item apply of one bulk-edit round.

    Every item in ``item_ids`` is attempted independently; one item's failure
    does not stop the rest from being attempted (design.md Decision 7).
    Returns a list of ``{"item", "success", "error"}`` dicts in ``item_ids``
    order, omitting any id that no longer resolves to an existing item.
    """
    product_line_adds = _resolve_suggestion_subsets(
        item_ids, cleaned_data.get("expected_product_line_suggestions")
    )
    category_adds = _resolve_suggestion_subsets(
        item_ids, cleaned_data.get("expected_category_suggestions")
    )
    tags_add = list(cleaned_data.get("tags_add") or [])
    tags_remove = list(cleaned_data.get("tags_remove") or [])
    metadata_provider_key = cleaned_data.get("metadata_provider_key", BULK_EDIT_LEAVE)
    priority = cleaned_data.get("priority")
    active = cleaned_data.get("active")

    items_by_pk = {
        item.pk: item
        for item in SearchableItem.objects.filter(pk__in=item_ids).prefetch_related("tags")
    }

    results = []
    for pk in item_ids:
        item = items_by_pk.get(pk)
        if item is None:
            continue
        try:
            with transaction.atomic():
                _apply_bulk_edit_to_item(
                    item,
                    priority=priority,
                    active=active,
                    tags_add=tags_add,
                    tags_remove=tags_remove,
                    metadata_provider_key=metadata_provider_key,
                    expected_product_line_add=product_line_adds.get(pk, []),
                    expected_category_add=category_adds.get(pk, []),
                )
            results.append({"item": item, "success": True, "error": ""})
        except Exception as exc:
            results.append({"item": item, "success": False, "error": str(exc)})
    return results
