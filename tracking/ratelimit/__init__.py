"""Rate-limit awareness for JSON/GraphQL sources (openspec change api-rate-limit-awareness).

Public surface:

* ``profiles``: ``get_profile``, ``PROFILES``, ``PROFILE_CHOICES``, ``default_scope``.
* ``budget``: ``MeterSnapshot``, ``BudgetSnapshot``.
* ``store``: ``BudgetStore``, ``InMemoryBudgetStore``, ``RedisBudgetStore``, ``get_budget_store``.
* ``pacer``: ``RateLimitPolicy``, ``PaceDecision``, ``try_acquire``, ``READY``/``SHORT_WAIT``/``DEFER``.
* ``reconcile``: ``reconcile_response``, ``record_429``.
* ``extractors``: ``parse_retry_after`` plus the per-profile extractors.
"""

from .budget import BudgetSnapshot, MeterSnapshot
from .extractors import (
    extract_graphql_cost,
    extract_ietf,
    extract_x_ratelimit,
    parse_retry_after,
)
from .pacer import DEFER, READY, SHORT_WAIT, PaceDecision, RateLimitPolicy, try_acquire
from .profiles import PROFILE_CHOICES, PROFILES, default_scope, get_profile
from .reconcile import reconcile_response, record_429
from .store import BudgetStore, InMemoryBudgetStore, RedisBudgetStore, get_budget_store

__all__ = [
    "BudgetSnapshot",
    "MeterSnapshot",
    "extract_graphql_cost",
    "extract_ietf",
    "extract_x_ratelimit",
    "parse_retry_after",
    "DEFER",
    "READY",
    "SHORT_WAIT",
    "PaceDecision",
    "RateLimitPolicy",
    "try_acquire",
    "PROFILE_CHOICES",
    "PROFILES",
    "default_scope",
    "get_profile",
    "reconcile_response",
    "record_429",
    "BudgetStore",
    "InMemoryBudgetStore",
    "RedisBudgetStore",
    "get_budget_store",
]
