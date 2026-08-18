# Rate-limit awareness (`rate_limit_profile`)

This guide is for operators configuring or debugging API-backed `Source`
rows. It covers what to set, what Redis buys you in production, and where to
look when a scrape is deferring or giving up. For the internal design
(pacing algorithm, atomic reserve, fan-out mechanics), see
`openspec/changes/api-rate-limit-awareness/design.md`.

## What `rate_limit_profile` does

Each `Source` has a `rate_limit_profile` field, editable on the Source
add/edit form and visible as a column in the Django admin. It selects how
that source's requests are paced:

| Profile | Meaning |
|---------|---------|
| *(blank)* / `none` | No dynamic awareness — fixed delay only (`SCRAPE_REQUEST_DELAY_SECONDS` + jitter), same behavior as before this feature. This is the default and is what plain HTML sources should use. |
| `ietf` | Reads standard `RateLimit-*` / `RateLimit` response headers (IETF draft). |
| `x-ratelimit` | Reads `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` (epoch-seconds reset, unlike the IETF delta form). |
| `graphql_cost` | Reads a GraphQL `extensions.cost` throttle block (e.g. Shopify Storefront API cost/throttle status) and paces on a `cost` meter instead of raw request count. |

Only set a profile on a source you know exposes one of these shapes.
Misconfiguring a profile (a typo, or a value not in the list above) fails
loudly: the unit's `FetchJob` records `config_error` rather than silently
falling back to fixed-delay pacing.

Profiled sources also change how HTTP 429 is handled: instead of the
generic urllib3 retry-and-eventually-`blocked` behavior that plain HTML
sources use, a 429 on a profiled source is surfaced to the pacer, which
reads `Retry-After` (when present), cools down that source's budget, and
defers the unit — it does **not** get recorded as `blocked` (see the
`give_up` section below for what it does become).

## Redis: required for real multi-worker pacing and locks

Rate-limit budgets (remaining requests/cost, next-allowed-at, cooldowns) and
the per-unit idempotency locks that prevent duplicate scheduling both live
in a shared store keyed by `REDIS_URL`:

- **`REDIS_URL` set** → budgets and locks live in Redis, shared across every
  Huey worker process. This is what production needs, especially once
  `workers` is more than 1 — without a shared store, each worker has its own
  idea of "how much quota is left," and two workers can both decide it's
  safe to send when only one request's worth of quota remains.
- **`REDIS_URL` unset** → both fall back to an in-memory store scoped to a
  single process. This is fine for local dev and for the test suite (which
  always runs this way), but it is **not** correct for a production
  deployment running more than one Huey worker — pacing state and locks
  would not be shared, defeating the point.

If you already run Huey against Redis for scheduling (see
`docs/scheduling.md`), no extra setup is needed — rate limiting reuses the
same `REDIS_URL`.

## `RATE_LIMIT_*` settings

These are global — one policy applies to every profiled source, there is no
per-Source tuning. All are documented with their defaults in `.env_sample`;
uncomment and override there if you need to change them:

- **`RATE_LIMIT_HEADROOM_PCT`** (default `0.50`) — fraction of a vendor's
  reported "remaining" quota to hold back as safety margin. At the default,
  only half of what the vendor says is left is ever actually usable.
- **`RATE_LIMIT_MIN_INTERVAL_SECONDS`** (default: same as
  `SCRAPE_REQUEST_DELAY_SECONDS`) — floor on the gap between requests to the
  same source, regardless of how much quota remains.
- **`RATE_LIMIT_FAIR_INTERVAL`** (default `True`) — when on, spreads
  remaining usable quota evenly across the time left until the vendor's
  reset, rather than bursting through it immediately.
- **`RATE_LIMIT_SHORT_WAIT_THRESHOLD_SECONDS`** (default `5.0`) — the
  cutoff between the two ways a worker can wait: below this, the worker
  sleeps in place and re-checks; at or above it, the unit **defers**
  (requeues for later) instead of holding a worker hostage.
- **`RATE_LIMIT_MAX_REQUESTS_PER_RUN`** / **`RATE_LIMIT_MAX_COST_PER_RUN`**
  (defaults `10000` / `100000`) — hard per-`WebUpdate` caps as a last-resort
  safety net; high enough that normal runs never hit them.
- **`RATE_LIMIT_MAX_DEFER_ATTEMPTS`** (default `5`) — how many times a unit
  will requeue itself after a defer before giving up.
- **`RATE_LIMIT_MAX_RUN_WALL_CLOCK_SECONDS`** (default `1800`, 30 minutes) —
  a unit gives up once its `WebUpdate` has been running this long, even if
  it hasn't hit the attempt cap yet, so a stuck run can't wait forever.

## Fan-out: one Huey task per source, not per run

Every scrape run (scheduled or manual) enqueues **one Huey task per
`ItemSource`** (item × source pair), not a single task that loops over
everything. Operationally this means:

- **Queue depth goes up.** A run touching 200 item-sources enqueues 200
  tasks, not 1. This is expected and is what makes the rest of this work —
  see the next point.
- **A rate-limited or deferring source no longer blocks unrelated ones.**
  Previously, one slow/rate-limited vendor inside a run's loop stalled
  everything behind it. Now that source's units defer and requeue
  themselves independently while workers keep processing every other
  source's units. Progress (`completed_searches`, result counts, etc.) on
  the `WebUpdate` still reflects the whole run as before.

## `give_up`: rate limiting gave up, not a vendor block

`FetchJob.Status.GIVE_UP` ("Gave up") shows up as its own badge in the
fetch-job table. It means a profiled unit deferred repeatedly — hit
`RATE_LIMIT_MAX_DEFER_ATTEMPTS` or `RATE_LIMIT_MAX_RUN_WALL_CLOCK_SECONDS` —
and the run closed it out rather than requeuing indefinitely. This is
**distinct from `blocked`**, which means the vendor actively refused the
request (403, or a block/challenge page). `give_up` is "we stopped waiting
on quota"; `blocked` is "they said no." Treat repeated `give_up` on one
source as a sign to revisit its profile, `RATE_LIMIT_*` policy, or how much
that source is being asked to do per run — not as a sign the vendor is
blocking you.

## Observability: structured logs only

There is no UI for remaining quota, cooldowns, or defer history for this
MVP — structured log lines are the only surface. Grep the Huey
worker/app logs for:

- `ratelimit try_acquire scope=... kind=... reason=... wait_ms=... eta=...` —
  every pacing decision (Ready / short_wait / defer), including which meter
  was constraining and why (`headroom`, `min_interval`, `fair_interval`,
  `retry_after`, `per_run_cap`).
- `ratelimit scope=... exhausted until ...` — a 429 `Retry-After` cooldown
  was recorded for that scope.
- `ratelimit give_up webupdate=... item_source=... attempt=... reason=...` —
  a unit stopped deferring and terminalized as `give_up`.

`scope` is `source:{source.key}` by default, so filtering logs by a
source's key is the quickest way to see everything happening to it during a
run.
