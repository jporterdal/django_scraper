import re

from django import forms

from .models import ItemSource, Source, UpdateSchedule
from .parsers import sources as parser_registry


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

        for name, field in self.fields.items():
            css_class = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css_class + " form-control").strip()

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

    class Meta:
        model = Source
        fields = [
            "key",
            "name",
            "parser_key",
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

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = (existing + " " + css).strip()
            if name in ("request_body_template", "request_headers"):
                widget.attrs.setdefault("class", "")
                widget.attrs["class"] = (
                    widget.attrs["class"] + " font-monospace"
                ).strip()

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

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = (existing + " " + css).strip()
