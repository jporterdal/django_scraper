"""Post-response store updates for profiled sends (D3 "after response", D6/task 2.5).

Bridges a raw ``requests.Response`` to the store: extract meters after a
normal response, or apply a cooldown after a 429. Kept separate from the
``scrape.py``/``fetcher.py`` call sites that will invoke these (fan-out
wiring is a later change) so the rate-limit machinery is usable and testable
on its own.
"""

import logging
import time

from .extractors import parse_retry_after

logger = logging.getLogger(__name__)


def reconcile_response(store, scope, profile, response, now=None):
    """Extract budget meters from a successful profiled response and store them.

    Returns the ``learned_cost`` reported by the profile's extractor (or
    ``None``), so a caller estimating GraphQL cost can update its own
    per-call estimate for subsequent requests.
    """
    now = now if now is not None else time.time()
    meters, learned_cost = profile.extract(response, now=now)
    if meters:
        store.update_meters(scope, meters, learned_cost=learned_cost)
    return learned_cost


def record_429(store, scope, response, now=None):
    """Apply a 429's ``Retry-After`` as a hard cooldown on ``scope`` (D6).

    No-ops (with a warning) when the response carries no ``Retry-After`` —
    there is nothing to zero the store to without inventing a cooldown.
    Returns the cooldown epoch, or ``None`` if none was applied.
    """
    now = now if now is not None else time.time()
    until_ts = parse_retry_after(response, now=now)
    if until_ts is None:
        logger.warning(
            "ratelimit 429 for scope=%s had no usable Retry-After header", scope
        )
        return None
    store.set_exhausted_until(scope, until_ts)
    logger.info("ratelimit scope=%s exhausted until %s (retry_after)", scope, until_ts)
    return until_ts
