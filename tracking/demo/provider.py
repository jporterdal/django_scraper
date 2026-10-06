"""Local-file metadata provider used in demo mode (design D8)."""

from django.templatetags.static import static

from ..matching import _normalize_for_match
from ..metadata_providers import (
    Candidate,
    MetadataProvider,
    ResolutionResult,
    ResolutionStatus,
)
from . import dataset


class DemoMetadataProvider(MetadataProvider):
    """Resolves items against the bundled ``metadata.json`` entries.

    A term contained in exactly one entry name is a match, in several entries
    needs review, in none is no-match: the same outcomes a real provider gives.
    """

    def resolve(self, item):
        term = _normalize_for_match(item.text)
        if not term:
            return ResolutionResult(status=ResolutionStatus.NO_MATCH)
        hits = [
            entry
            for entry in dataset.metadata_entries()
            if term in _normalize_for_match(entry["name"])
        ]
        if not hits:
            return ResolutionResult(status=ResolutionStatus.NO_MATCH)
        if len(hits) == 1:
            return ResolutionResult(
                status=ResolutionStatus.MATCHED,
                external_id=hits[0]["id"],
                payload=dict(hits[0]),
            )
        return ResolutionResult(
            status=ResolutionStatus.NEEDS_REVIEW,
            candidates=[Candidate(external_id=e["id"], payload=dict(e)) for e in hits],
        )

    def fetch_by_id(self, external_id):
        for entry in dataset.metadata_entries():
            if entry["id"] == external_id:
                return dict(entry)
        return None

    def to_display(self, payload):
        thumbnail = payload.get("thumbnail", "")
        thumbnail_url = static(thumbnail) if thumbnail else ""
        type_line = payload.get("type_line", "")
        text = payload.get("text", "")
        description = f"{type_line} — {text}" if type_line and text else (type_line or text)
        return {
            "thumbnail_url": thumbnail_url,
            "description": description,
            # Self-hosted: the full-size demo card image.
            "external_url": thumbnail_url,
        }
