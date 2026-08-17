import re

from django import forms
from django.db import transaction
from django.db.models.functions import Lower
from django.forms import BaseFormSet, formset_factory

from .models import ItemSource, SearchableItem, Source, Tag, UpdateSchedule
from .parsers import sources as parser_registry
from .ratelimit import PROFILE_CHOICES as rate_limit_profile_choices

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

PINNED_URL_HELP_TEXT = (
    "When set, the scraper fetches this URL directly with GET instead of building "
    "a search URL from the source template. Use for stubborn listings where search "
    "does not return the right result. The source's parser must be able to parse "
    "the pinned endpoint's response."
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
    """Add Bootstrap widget classes to every field on a form."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            css = "form-check-input"
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            css = "form-select"
        else:
            css = "form-control"
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = (existing + " " + css).strip()


class SearchableItemForm(forms.ModelForm):
    """Form for editing a SearchableItem (search term, priority, active, tags)."""

    class Meta:
        model = SearchableItem
        fields = ["text", "priority", "active", "tags"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_form_classes(self)


class SearchableItemCreateForm(SearchableItemForm):
    """Create view: search text only."""

    class Meta(SearchableItemForm.Meta):
        fields = ["text"]


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


def create_items_from_bulk_add(terms, tag, priority, source_forms):
    """Atomically create SearchableItems (and optional ItemSources) from bulk add.

    ``source_forms`` should be validated ``ItemSourceForm`` instances. Empty or
    deleted rows are skipped. Returns the list of created ``SearchableItem``s.
    """
    created = []
    with transaction.atomic():
        for term in terms:
            item = SearchableItem.objects.create(text=term, priority=priority)
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
            created.append(item)
    return created
