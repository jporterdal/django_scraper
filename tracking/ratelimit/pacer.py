"""Pacing policy and the ``try_acquire`` decision (D3/D4/D5/D13 in design.md).

``decide()`` is the single source of truth for the Ready / ShortWait / Defer
algorithm (headroom, min/fair interval, multi-meter gating, per-run caps).
``InMemoryBudgetStore`` calls it directly under a lock; ``RedisBudgetStore``
runs an equivalent Lua script server-side (see ``store.RedisBudgetStore``) —
keep the two in sync when changing either.
"""

import logging
import math
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

READY = "ready"
SHORT_WAIT = "short_wait"
DEFER = "defer"


@dataclass(frozen=True)
class RateLimitPolicy:
    """Global safety-policy defaults (D4). One instance covers every scope."""

    headroom_pct: float = 0.50
    min_interval: float = 3.0
    fair_interval: bool = True
    short_wait_threshold: float = 5.0
    max_requests_per_run: int = 10_000
    max_cost_per_run: int = 100_000
    max_defer_attempts: int = 5
    max_run_wall_clock_seconds: float = 1800.0

    @classmethod
    def from_settings(cls):
        from django.conf import settings

        return cls(
            headroom_pct=getattr(settings, "RATE_LIMIT_HEADROOM_PCT", 0.50),
            min_interval=getattr(
                settings,
                "RATE_LIMIT_MIN_INTERVAL_SECONDS",
                getattr(settings, "SCRAPE_REQUEST_DELAY_SECONDS", 3.0),
            ),
            fair_interval=getattr(settings, "RATE_LIMIT_FAIR_INTERVAL", True),
            short_wait_threshold=getattr(
                settings, "RATE_LIMIT_SHORT_WAIT_THRESHOLD_SECONDS", 5.0
            ),
            max_requests_per_run=getattr(
                settings, "RATE_LIMIT_MAX_REQUESTS_PER_RUN", 10_000
            ),
            max_cost_per_run=getattr(
                settings, "RATE_LIMIT_MAX_COST_PER_RUN", 100_000
            ),
            max_defer_attempts=getattr(settings, "RATE_LIMIT_MAX_DEFER_ATTEMPTS", 5),
            max_run_wall_clock_seconds=getattr(
                settings, "RATE_LIMIT_MAX_RUN_WALL_CLOCK_SECONDS", 1800
            ),
        )

    def max_per_run(self):
        """Per-run caps keyed by meter unit, for the units this policy knows about."""
        return {"request": self.max_requests_per_run, "cost": self.max_cost_per_run}


@dataclass(frozen=True)
class PaceDecision:
    """Result of a ``try_acquire`` call.

    ``kind`` is one of ``READY`` / ``SHORT_WAIT`` / ``DEFER``. ``wait_seconds``
    is only meaningful for ``SHORT_WAIT`` (sleep, then re-``try_acquire``).
    ``eta`` (epoch seconds) is only meaningful for ``DEFER`` (requeue at/after
    this time); ``None`` eta on a Defer means "no useful eta" (e.g. a
    per-run-cap trip) — the caller should give up rather than requeue tightly.
    ``fallback=True`` on a Ready means no meters were known yet for this scope
    (cold start): the caller should also apply the fixed-delay fallback (D4)
    since nothing was actually paced.
    """

    kind: str
    wait_seconds: float = 0.0
    eta: float | None = None
    reason: str = ""
    unit: str = ""
    fallback: bool = False


def decide(snapshot, run_spent, costs, policy, now):
    """Pure Ready/ShortWait/Defer decision (D3/D4/D5). No mutation.

    ``snapshot`` is a ``budget.BudgetSnapshot`` (read-only here — the caller
    applies the reservation iff this returns Ready). ``run_spent`` is a
    ``dict[unit] -> already-spent-this-run`` mapping. ``costs`` is
    ``dict[unit] -> estimated cost of the call for that meter``.
    """
    if snapshot.exhausted_until is not None and now < snapshot.exhausted_until:
        return PaceDecision(
            kind=DEFER, eta=snapshot.exhausted_until, reason="retry_after"
        )

    max_per_run = policy.max_per_run()
    for unit, cost in costs.items():
        cap = max_per_run.get(unit)
        spent = run_spent.get(unit, 0)
        if cap is not None and spent + cost > cap:
            return PaceDecision(kind=DEFER, eta=None, reason="per_run_cap", unit=unit)

    any_known = False
    max_wait = 0.0
    reason = "min_interval"
    winning_unit = ""

    for unit, cost in costs.items():
        meter = snapshot.meters.get(unit)
        if meter is None or meter.remaining is None:
            continue
        any_known = True

        usable = math.floor(meter.remaining * (1 - policy.headroom_pct))
        if usable < cost:
            if meter.reset_at is not None:
                wait = max(0.0, meter.reset_at - now)
            else:
                # Exhausted with no known reset: back off by min_interval and
                # re-check rather than inventing a reset time.
                wait = max(policy.min_interval, 30.0)
            if wait >= max_wait:
                max_wait, reason, winning_unit = wait, "headroom", unit
            continue

        wait = max(0.0, snapshot.next_allowed_at - now)
        this_reason = "min_interval"
        if policy.fair_interval and meter.reset_at is not None and usable > 0:
            fair = (meter.reset_at - now) / max(usable, 1)
            if fair > wait:
                wait, this_reason = fair, "fair_interval"
        if wait >= max_wait:
            max_wait, reason, winning_unit = wait, this_reason, unit

    if not any_known:
        # Cold start: nothing known yet for any requested meter. Ready, but
        # flagged so the caller also applies the fixed-delay fallback (D4) —
        # there is no reservation to make against unknown remaining.
        return PaceDecision(kind=READY, fallback=True)

    if max_wait <= 0:
        return PaceDecision(kind=READY)
    if max_wait < policy.short_wait_threshold:
        return PaceDecision(
            kind=SHORT_WAIT, wait_seconds=max_wait, reason=reason, unit=winning_unit
        )
    return PaceDecision(
        kind=DEFER, eta=now + max_wait, reason=reason, unit=winning_unit
    )


def try_acquire(store, scope, costs, run_id, policy=None, now=None):
    """Atomic check-and-reserve against ``store`` for ``scope`` (D13).

    ``costs`` is ``dict[unit] -> estimated cost`` for the call about to be
    made (e.g. ``{"request": 1}`` or ``{"request": 1, "cost": 15}``). Returns
    a ``PaceDecision``. Only a ``READY`` decision reserves anything in the
    store; ``SHORT_WAIT``/``DEFER`` never mutate state, so the caller must
    re-invoke ``try_acquire`` after sleeping rather than sending on the
    strength of a stale decision.
    """
    policy = policy or RateLimitPolicy.from_settings()
    now = now if now is not None else time.time()

    decision = store.try_acquire(scope, costs, run_id, policy, now)

    logger.info(
        "ratelimit try_acquire scope=%s kind=%s reason=%s unit=%s "
        "wait_ms=%s eta=%s fallback=%s",
        scope,
        decision.kind,
        decision.reason,
        decision.unit,
        int(decision.wait_seconds * 1000),
        decision.eta,
        decision.fallback,
    )
    return decision
