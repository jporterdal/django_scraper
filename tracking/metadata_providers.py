"""Metadata-provider registry (mirrors tracking/parsers.py's registry pattern).

Every provider maps a ``SearchableItem`` to external reference metadata via a
pure code contract:

* ``resolve(item) -> ResolutionResult`` — matched / needs-review / no-match.
* ``to_display(payload) -> {thumbnail_url, description, external_url}`` — a
  pure function over a stored (or candidate) payload, computed at render
  time so the generic templates never see provider-specific fields.
* ``fetch_by_id(external_id)`` — used for a ``pinned_external_id`` (a
  disambiguation pick or manual entry) instead of re-running ``resolve()``.

See the item-metadata-enrichment capability spec for the full contract.
"""

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# Scryfall's API guidelines ask for a descriptive User-Agent identifying the
# application (not a browser UA) on every request.
SCRYFALL_USER_AGENT = "django-scraper/1.0 (item metadata enrichment)"


class ResolutionStatus:
    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    NO_MATCH = "no_match"


@dataclass
class Candidate:
    """One disambiguation candidate: an external id plus its raw payload."""

    external_id: str
    payload: dict = field(default_factory=dict)


@dataclass
class ResolutionResult:
    """Outcome of ``MetadataProvider.resolve()``.

    Exactly one of: a confident match (``status=MATCHED``, ``external_id`` +
    ``payload`` set), ambiguous review candidates (``status=NEEDS_REVIEW``,
    ``candidates`` set), or nothing found (``status=NO_MATCH``).
    """

    status: str
    external_id: str = ""
    payload: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)


class MetadataProvider:
    """Base contract every registered provider implements."""

    def resolve(self, item):
        """Resolve ``item`` (a ``SearchableItem``) to a ``ResolutionResult``."""
        raise NotImplementedError

    def to_display(self, payload):
        """Map a raw ``payload`` dict to ``{thumbnail_url, description, external_url}``."""
        raise NotImplementedError

    def fetch_by_id(self, external_id):
        """Fetch the raw payload for a known ``external_id``, or ``None`` if not found.

        Used when an item has a ``pinned_external_id`` (from a candidate pick
        or manual entry) instead of re-running ``resolve()``.
        """
        raise NotImplementedError


class ScryfallProvider(MetadataProvider):
    """Resolves a SearchableItem against the Scryfall card search API.

    ``resolve()`` searches by the item's ``text``; Scryfall's search collapses
    multiple printings into one card identity, so a single result is expected
    to be the common case, but the plumbing treats multiple results as an
    ordinary ``NEEDS_REVIEW`` outcome rather than an edge case.
    """

    SEARCH_URL = "https://api.scryfall.com/cards/search"
    CARD_URL = "https://api.scryfall.com/cards/{id}"
    REQUEST_TIMEOUT = 30

    def _headers(self):
        return {"User-Agent": SCRYFALL_USER_AGENT, "Accept": "application/json"}

    def resolve(self, item):
        response = requests.get(
            self.SEARCH_URL,
            params={"q": item.text},
            headers=self._headers(),
            timeout=self.REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            return ResolutionResult(status=ResolutionStatus.NO_MATCH)
        response.raise_for_status()

        cards = response.json().get("data", [])
        if not cards:
            return ResolutionResult(status=ResolutionStatus.NO_MATCH)

        if len(cards) == 1:
            card = cards[0]
            return ResolutionResult(
                status=ResolutionStatus.MATCHED,
                external_id=str(card.get("id", "")),
                payload=card,
            )

        candidates = [
            Candidate(external_id=str(card.get("id", "")), payload=card)
            for card in cards
        ]
        return ResolutionResult(status=ResolutionStatus.NEEDS_REVIEW, candidates=candidates)

    def fetch_by_id(self, external_id):
        response = requests.get(
            self.CARD_URL.format(id=external_id),
            headers=self._headers(),
            timeout=self.REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def to_display(self, payload):
        payload = payload or {}

        image_uris = payload.get("image_uris") or {}
        if not image_uris:
            faces = payload.get("card_faces") or []
            if faces:
                image_uris = faces[0].get("image_uris") or {}
        thumbnail_url = image_uris.get("normal") or image_uris.get("small") or ""

        description = payload.get("oracle_text", "")
        if not description:
            faces = payload.get("card_faces") or []
            description = "\n\n".join(
                face.get("oracle_text", "") for face in faces if face.get("oracle_text")
            )

        external_url = payload.get("scryfall_uri", "")

        return {
            "thumbnail_url": thumbnail_url,
            "description": description,
            "external_url": external_url,
        }


PROVIDERS = {
    "scryfall": ScryfallProvider,
}
