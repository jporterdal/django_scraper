import re

from django import forms

from .models import ItemSource, Source
from .parsers import sources as parser_registry


BASE_SEARCH_URL_HELP_TEXT = (
    "Search URL template; use {term} for the URL-encoded query string. "
    "Example: https://example.com/search?q={term}"
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
            "title_include_patterns",
            "title_exclude_patterns",
        ]

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
            "base_search_url",
            "request_headers",
            "page_size",
        ]
        help_texts = {
            "base_search_url": BASE_SEARCH_URL_HELP_TEXT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            css_class = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css_class + " form-control").strip()

        # key is the primary key: allow it on create, lock it on edit.
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk:
            self.fields["key"].disabled = True
            self.fields["key"].help_text = "The key cannot be changed once created."

    def clean_base_search_url(self):
        url = self.cleaned_data.get("base_search_url", "")
        if "{term}" not in url:
            raise forms.ValidationError(
                "The search URL must contain '{term}' so the query can be inserted."
            )
        return url
