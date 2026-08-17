"""Rate-limit profile registry, parallel to ``parsers.sources`` (D1/task 1.3).

A ``Source.rate_limit_profile`` of ``""``/``"none"`` means "no dynamic
awareness — fixed delay only" (the default). Any other value must be a
registered key here; ``get_profile`` raises ``KeyError`` for unknown keys so
misconfiguration fails loudly rather than silently disabling pacing.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from . import extractors


@dataclass(frozen=True)
class RateLimitProfile:
    key: str
    #: response -> (meters, learned_cost); see extractors.py.
    extract: Callable
    #: urllib3 status codes this profile's sends should let urllib3 retry.
    #: API profiles exclude 429 — that must surface to the pacer, not be
    #: retried transparently (D6).
    retry_status_forcelist: tuple = (503,)
    #: Optional dict[unit] -> conservative default cost estimate to use
    #: before the first response has taught us the real cost (D4).
    default_cost: Optional[dict] = None


PROFILES = {
    "ietf": RateLimitProfile(key="ietf", extract=extractors.extract_ietf),
    "x-ratelimit": RateLimitProfile(
        key="x-ratelimit", extract=extractors.extract_x_ratelimit
    ),
    "graphql_cost": RateLimitProfile(
        key="graphql_cost",
        extract=extractors.extract_graphql_cost,
        default_cost={"cost": 1},
    ),
}

# Choices for the Source form / admin dropdown. "" is the default (no dynamic
# awareness); registered profile keys are appended in registration order.
PROFILE_CHOICES = [("", "None (fixed delay only)")] + [
    (key, key) for key in PROFILES
]


def get_profile(key):
    """Return the registered ``RateLimitProfile`` for ``key``, or ``None``.

    ``None`` means "no dynamic rate-limit awareness" — both the blank default
    and the explicit ``"none"`` alias map to it. Unknown non-empty keys raise
    ``KeyError`` (mirrors ``parsers.sources[source.parser_key]`` lookups).
    """
    normalized = (key or "").strip().lower()
    if normalized in ("", "none"):
        return None
    return PROFILES[normalized]


def default_scope(source):
    """Default rate-limit scope for a ``Source``: ``source:{key}`` (D2)."""
    return f"source:{source.key}"
