"""Per-profile budget extractors (D1/task 2.3/2.4).

Every extractor has the signature ``extract(response, now=None) ->
(meters, learned_cost)``:

* ``meters`` — ``dict[unit] -> MeterSnapshot`` for whatever the response
  revealed (empty dict when nothing usable was present — callers must not
  invent a value; see ``pacer.decide``'s cold-start handling).
* ``learned_cost`` — ``dict[unit] -> float`` estimated cost observed on
  *this* call (only meaningful for cost-style meters, e.g. GraphQL query
  cost), or ``None`` when the profile doesn't estimate cost.

``parse_retry_after`` is not profile-specific: any profiled source may see a
429 with a ``Retry-After`` header (D6).
"""

import time
from email.utils import parsedate_to_datetime

from .budget import MeterSnapshot


def _to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_ietf(response, now=None):
    """IETF ``RateLimit`` draft headers — reset is a **delta** in seconds.

    Supports both the combined ``RateLimit: limit=100, remaining=50, reset=30``
    form and the split ``RateLimit-Limit`` / ``RateLimit-Remaining`` /
    ``RateLimit-Reset`` headers.
    """
    now = now if now is not None else time.time()
    headers = response.headers

    combined = headers.get("RateLimit")
    if combined:
        parts = {}
        for kv in combined.split(","):
            if "=" not in kv:
                continue
            key, _, value = kv.strip().partition("=")
            parts[key.strip().lower()] = value.strip()
        limit = _to_number(parts.get("limit"))
        remaining = _to_number(parts.get("remaining"))
        reset_delta = _to_number(parts.get("reset"))
    else:
        limit = _to_number(headers.get("RateLimit-Limit"))
        remaining = _to_number(headers.get("RateLimit-Remaining"))
        reset_delta = _to_number(headers.get("RateLimit-Reset"))

    if remaining is None:
        return {}, None

    reset_at = now + reset_delta if reset_delta is not None else None
    return {
        "request": MeterSnapshot(
            unit="request", remaining=remaining, limit=limit, reset_at=reset_at
        )
    }, None


def extract_x_ratelimit(response, now=None):
    """GitHub/Twitter-style ``X-RateLimit-*`` headers — reset is an **epoch**."""
    now = now if now is not None else time.time()
    headers = response.headers

    limit = _to_number(headers.get("X-RateLimit-Limit"))
    remaining = _to_number(headers.get("X-RateLimit-Remaining"))
    reset_epoch = _to_number(headers.get("X-RateLimit-Reset"))

    if remaining is None:
        return {}, None

    return {
        "request": MeterSnapshot(
            unit="request", remaining=remaining, limit=limit, reset_at=reset_epoch
        )
    }, None


def extract_graphql_cost(response, now=None):
    """GraphQL ``extensions.cost`` throttle block (Shopify/GitHub-style).

    Expected shape::

        {"extensions": {"cost": {
            "requestedQueryCost": 15, "actualQueryCost": 12,
            "throttleStatus": {"maximumAvailable": 1000,
                                "currentlyAvailable": 700, "restoreRate": 50}
        }}}

    ``restoreRate`` (points/second) lets us derive a reset time for the fair
    interval calc: time to refill from ``currentlyAvailable`` back to
    ``maximumAvailable``.
    """
    now = now if now is not None else time.time()
    try:
        data = response.json()
    except ValueError:
        return {}, None

    if not isinstance(data, dict):
        return {}, None
    cost_block = (data.get("extensions") or {}).get("cost")
    if not isinstance(cost_block, dict):
        return {}, None

    throttle = cost_block.get("throttleStatus") or {}
    remaining = _to_number(throttle.get("currentlyAvailable"))
    limit = _to_number(throttle.get("maximumAvailable"))
    restore_rate = _to_number(throttle.get("restoreRate"))

    meters = {}
    if remaining is not None:
        reset_at = None
        if restore_rate and limit is not None and restore_rate > 0:
            deficit = max(0.0, limit - remaining)
            reset_at = now + (deficit / restore_rate)
        meters["cost"] = MeterSnapshot(
            unit="cost", remaining=remaining, limit=limit, reset_at=reset_at
        )

    actual_cost = _to_number(cost_block.get("actualQueryCost"))
    if actual_cost is None:
        actual_cost = _to_number(cost_block.get("requestedQueryCost"))
    learned_cost = {"cost": actual_cost} if actual_cost is not None else None

    return meters, learned_cost


def parse_retry_after(response, now=None):
    """``Retry-After`` header -> absolute epoch seconds, or ``None``.

    Accepts both the delta-seconds form (``Retry-After: 30``) and the
    HTTP-date form (``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT``).
    """
    now = now if now is not None else time.time()
    value = response.headers.get("Retry-After")
    if not value:
        return None
    value = value.strip()

    seconds = _to_number(value)
    if seconds is not None:
        return now + seconds

    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.timestamp()
