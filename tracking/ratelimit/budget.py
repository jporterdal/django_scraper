"""Budget snapshot data model shared by the in-memory and Redis stores.

A ``BudgetSnapshot`` is the shared pacing state for one rate-limit ``scope``
(default ``source:{source.key}``, see ``profiles.default_scope``): the last
known meters (``request`` / ``cost`` / ``token`` / ...) plus the bookkeeping
(``next_allowed_at``, ``exhausted_until``) ``try_acquire`` needs to decide
Ready / ShortWait / Defer without re-deriving it from raw header state.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MeterSnapshot:
    """Vendor-reported budget for one meter unit (``request``, ``cost``, ``token``).

    ``remaining``/``limit``/``reset_at`` are ``None`` when unknown (cold start,
    or the vendor did not report that field on this response) — callers must
    not invent a value when unknown; see ``pacer._decide``.
    """

    unit: str
    remaining: float | None
    limit: float | None = None
    # Epoch seconds (UTC) when this meter resets. ``None`` when the vendor
    # didn't report a reset (extractors normalize both delta-seconds and
    # epoch-seconds vendor formats into this absolute epoch form).
    reset_at: float | None = None


@dataclass
class BudgetSnapshot:
    """Shared pacing state for one rate-limit scope."""

    scope: str
    meters: dict = field(default_factory=dict)  # unit -> MeterSnapshot
    # Epoch seconds: earliest time the next request for this scope may send,
    # per min_interval pacing. Bumped on every Ready reservation.
    next_allowed_at: float = 0.0
    # Epoch seconds: hard cooldown from a 429 Retry-After (or usable-exhausted
    # with a known reset). ``None`` when not currently exhausted.
    exhausted_until: float | None = None
