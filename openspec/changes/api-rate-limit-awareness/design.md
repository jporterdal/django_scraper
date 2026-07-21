## Context

Today `tracking/fetcher.py` paces scrapes with a fixed `SCRAPE_REQUEST_DELAY_SECONDS` + jitter and retries **both** 429 and 503 via urllib3 without reading rate-limit headers or GraphQL cost extensions; `tracking/scrape.py` then treats page‑1 HTTP 429 like 403 (`blocked`). A schedule or manual update enqueues **one** `run_web_update_task` that loops all `ItemSource` rows for the selected items. Production Huey uses `workers: 2` (this may increase in future), so in-process quota state is wrong across workers, and any long sleep inside that loop holds a scarce worker while other tagged items’ sources (often overlapping vendors) wait.

This change adds rate-limit awareness for JSON/GraphQL sources **and** splits work into **one Huey task per ItemSource** so deferring a hot API frees workers for ready scopes. Plain HTML keeps fixed-delay pacing inside each unit. Broader multi-worker data-race correctness remains a follow-up where noted; ingest dedup under fan-out is per-unit (D12).

## Goals / Non-Goals

**Goals:**

- Pluggable extractors for REST-style headers and GraphQL `extensions` (room for token meters).
- Shared Redis budget store keyed by rate-limit scope.
- `try_acquire` → Ready | ShortWait (&lt; 5s sleep then re-acquire) | Defer (requeue with eta); atomic Lua reserve in Redis.
- Safety: 50% headroom, min interval, fair interval, global per-run caps, give-up, idempotent requeue.
- Multi-unit meters (`request`, `cost`, `token`); most constraining wins.
- Fan-out per ItemSource; DONE via last-finisher CAS + FetchJob count predicate (no sweeper).
- Structured logging MVP; offline tests with fakes.

**Non-Goals:**

- Dynamic rate-limit profiles on plain HTML sources.
- UI for remaining quota / cooldown.
- Per-Source operator tuning of headroom/intervals.
- Background sweeper to mark DONE.
- First-class gRPC/SOAP.
- Full concurrent-WebUpdate correctness project (separate change).
- Run-level dedup finalize / candidate staging (rejected; see D12).

## Decisions

### D1 — Profile registry parallel to parsers

**Choice:** Add `rate_limit_profile` on `Source`, default none/empty. Code-registered profiles (`none`, `ietf`, `x-ratelimit`, `graphql_cost`, …) like `parser_key`.

**Why:** Vendor shapes stay out of generic scrape logic; operators pick a profile without inventing numeric knobs.

### D2 — Redis BudgetStore as source of truth in production

**Choice:** Budget snapshots in Redis (`ratelimit:{scope_key}`). Scope defaults to `source:{source.key}` unless a profile defines a coarser key. In-memory/fake store under tests / when Redis unset for suite.

### D3 — try_acquire: short sleep vs defer (replaces sleep-always)

**Choice:** Before each profiled HTTP call, `try_acquire(scope, estimated_cost)` returns:

| Result | Behavior |
|--------|----------|
| Ready | Send request |
| ShortWait(ms) with ms &lt; **5s** | Sleep in-worker, then send |
| Wait ≥ **5s**, usable exhausted until reset, or 429 `Retry-After` | **Defer**: idempotent requeue with `eta`, return (worker free) |

After response: extract meters / Retry-After → BudgetStore → log.

HTML / `none` profile: `Fetcher.wait()` (or equivalent fixed delay) inside the unit; no budget store.

**Concurrency:** Ready is only returned from an atomic reserve (D13). ShortWait never implies “sleep then send”; the caller must re-enter `try_acquire`.

**Why:** Tagged runs mix overlapping sources; long sleeps must not park the whole catalog behind one hot API.

### D4 — Safety policy (global defaults)

| Knob | Default | Role |
|------|---------|------|
| `headroom_pct` | `0.50` | `usable = floor(remaining * (1 - headroom_pct))` |
| `min_interval` | `SCRAPE_REQUEST_DELAY_SECONDS` (+ existing jitter policy as applicable) | Floor; dedicated env override optional later |
| `fair_interval` | on | `fair ≈ (resets_at - now) / max(usable, 1)`; effective wait = max(min_interval, fair) |
| `short_wait_threshold` | **5s** | Below → sleep; at/above → Defer |
| `max_requests_per_run` / `max_cost_per_run` | **10000** / **100000** (high; document in `.env_sample`) | Cap one WebUpdate’s spend |
| Unknown meters | fixed delay | Cold start / missing headers |
| GraphQL cost estimate | Learn from first response’s actual/requested cost; before that use profile conservative default (e.g. 1 or profile constant) | |

### D5 — Multi-meter pacing

Allow the call only if every applicable meter has enough **usable** remaining for the estimated cost (`1` request; GraphQL cost from last known or profile default).

### D6 — 429 / Retry-After → Defer (profile-gated urllib3)

**Choice:** Split 429 handling by rate-limit profile at send time.

| Profile | urllib3 `status_forcelist` | App behavior on HTTP 429 |
|---------|----------------------------|---------------------------|
| `none` / empty (HTML-only) | Keep **429** and **503** (today’s opaque retry) | Unchanged: after adapter retries exhaust, page‑1 non-200 still classifies `blocked` when status is 403 or 429 |
| API profiles (`ietf`, `x-ratelimit`, `graphql_cost`, …) | **503 only** — do **not** retry 429 in urllib3 | Surface the first 429 to the unit; parse `Retry-After` when present; zero usable in shared store until cooldown; **Defer** (or give-up per D10). Do **not** long-sleep the worker. Do **not** terminalize as `blocked`. |

**`scrape.py` rewrite (explicit):** Today `_run_parser_search` sets
`blocked=response.status_code in (403, 429)` on page‑1 non-200, and
`run_web_update` maps `outcome.blocked` → `FetchJob.Status.BLOCKED`.
For **profiled** units that path MUST change:

1. After a profiled send returns HTTP 429, run the D6 store update + Defer/give-up path **instead of** returning `FetchOutcome(blocked=True)`.
2. Stop treating 429 as a vendor-block signal for profiled sources: `blocked` remains for **403** and blocked-body / WAF detection only (D15).
3. HTML / `none` keeps the existing `(403, 429) → blocked` classification (after urllib3 retries).

urllib3 remains a backstop for transient **503** on all profiles.

### D7 — Observability

Structured logs only (`scope`, units, remaining, usable, sleep_ms / eta, reason: `headroom` | `min_interval` | `fair_interval` | `retry_after` | `per_run_cap` | `fixed_fallback` | `defer` | `give_up`). No UI.

### D8 — Unit of work = one Huey task per ItemSource (Option B)

**Choice:** Schedule/manual update creates one `WebUpdate(PENDING, total_searches=N)` then enqueues **N** `fetch_one`-style tasks `(webupdate_id, item_source_id)`.

Each task: idempotency guards → `try_acquire` → fetch/parse/store path for that pair → `terminalize` (FetchJob + counters) or Defer/give-up.

**Why:** Usual load is many tagged items × overlapping sources; per-unit tasks let ready sources proceed while one scope defers.

### D9 — DONE = last-finisher CAS + FetchJob predicate (no sweeper)

**Choice:**

- Exactly one terminal `FetchJob` per `(webupdate, item, source)` (DB **unique** constraint).
- Defer does **not** write FetchJob or bump `completed_searches`.
- On terminalize: insert FetchJob; `F()` bump counters; PENDING→RUNNING on first start; if `COUNT(FetchJob for webupdate) >= total_searches` (equivalently counters), CAS `RUNNING → DONE`.
- Unit `try/finally` always terminalizes on unexpected errors (failed FetchJob + bump).
- `WebUpdate.FAILED` reserved for orchestrator/fan-out catastrophe, not a single unit error.

**No sweeper:** barrier closes only via terminalize (success, error, or give-up).

### D10 — Give-up policy (hot path, closes barrier)

**Choice:** Before requeue on Defer, `should_give_up` if **either**:

- `attempt >= 5` (`MAX_DEFER_ATTEMPTS`), or
- `now - webupdate.timestamp >= 30 minutes` (`MAX_RUN_WALL_CLOCK`)

Then `terminalize` with FetchJob status **`give_up`** (new status; counts as completed; bumps `error_count`), log `reason=give_up`. Do **not** reuse `blocked` — that remains for vendor 403/WAF/blocked-body responses.

Tunable via settings/env later; these are MVP defaults.

### D11 — Idempotent enqueue / requeue

**Choice:**

1. **Redis `SET NX` lock** keyed `fetchone:{webupdate_id}:{item_source_id}` with TTL = `max(eta - now, 0) + 120s` grace (minimum TTL 300s when eta is immediate). Failed SET → skip duplicate enqueue.
2. On Defer: delete lock, then SET NX + schedule once with `attempt+1`.
3. On terminalize: delete lock.
4. **DB unique** on `FetchJob(webupdate, item, source)` — duplicate delivery that inserts again is a no-op (no second counter bump).
5. Custom Huey `task_id` optional if easy with djhuey; **not required** for MVP if Redis NX + unique FetchJob are in place.

### D12 — Per-unit dedup at terminalize (not run-level finalize)

**Choice:** Move today’s `_deduplicate_result_kwargs` / snapshot logic into each unit’s terminalize path. Same predicates: key `(item_id, source_id, title)`, compare `(price, instock)` against prior-run snapshots and within-unit duplicates. Persist survivors in-unit; set `FetchJob.stored_count`; `F()` bump `WebUpdate.result_count` and `skipped_duplicate_count`. Progress polling reflects stored/skipped at terminalize only (no pre-dedup inflation).

**Why equivalent under Option B:** `ItemSource` is unique on `(item, source)`, so each Huey unit owns a disjoint dedup key space. Cross-unit “within-batch” dedup is a no-op by construction; run-level finalize would add staging + a second barrier for no semantic gain and fights the no-sweeper DONE design.

**Double-store under fan-out:** Real risk is duplicate *delivery* of the same unit, not two different units colliding. Mitigated by D11 (Redis NX + unique FetchJob): store+dedup runs at most once per successful terminalize. Overlapping concurrent WebUpdates remain a separate correctness project.

**Snapshot query cost:** Per-unit query scoped to that item (optionally also `source_id`); N skinny queries accepted for MVP. No run-level Redis-cached snapshot map.

**Rejected:** Run-level candidate buffer + last-finisher or sweeper reduce.

### D13 — Atomic `try_acquire` (Lua check-and-reserve; mutex-shaped semantics)

**Choice:** `try_acquire(scope, estimated_cost, run_id)` is a single atomic check-and-reserve. Production Redis uses a **Lua script** (one round-trip) with the same semantics as holding a per-scope mutex: read meters / `next_allowed_at` / `exhausted_until` / run caps → decide → on **Ready only**, reserve estimated cost, bump `next_allowed_at`, and INCR per-run counters. In-memory/test store uses a threading lock and the same reserve logic.

| Result | Mutation | Caller |
|--------|----------|--------|
| Ready | Reserve cost + claim interval slot + run counters | Send; then extract headers → reconcile snapshot |
| ShortWait(ms), ms &lt; 5s | None | Sleep, then **call `try_acquire` again** (never send after sleep without re-check) |
| Defer(eta) | None | Requeue (D10/D11) |

**Why in this change:** Fan-out + `workers: 2` makes concurrent same-scope acquires the common case. Shared snapshots without atomic reserve let two workers both Ready when usable only allows one. This is pacing correctness, not the separate scrape/DONE multi-WebUpdate project. Overlapping runs that share a scope naturally share the same gate (scope-keyed budget).

**Rejected for MVP:** Scope mutex via separate `SET NX` lock around multi-step Redis reads/writes (same semantics, more round-trips). Pure token-bucket DECR (poor fit for headroom + fair interval + multi-meter + 429).

**Future (out of scope):** **Reserve refund** — if Ready reserved cost but the HTTP send fails before a useful response (e.g. connect timeout with no cost extensions), refund the reservation so other workers are not blocked on phantom spend. Most painful when estimated GraphQL cost is large relative to usable and no header reconcile runs. Headroom (50%) cushions request meters for MVP; revisit in a follow-up if cost-meter false Defers appear in production.

### D14 — Pagination: one unit, try_acquire per page, Defer restarts from page 1

**Choice (MVP):** The Huey unit remains one `ItemSource` task (not one task per page). Before each page HTTP call, profiled sources call `try_acquire`; HTML/`none` keep fixed delay between pages. Defer only at **page boundaries** (never mid-parse). Mid-pagination resume cursor is out of scope.

**On Defer after early pages succeeded:** Discard in-memory results for that attempt, requeue the unit (D10/D11), and restart from page 1 on the next attempt. Early pages may re-spend quota; D12 dedup skips identical stored rows. This differs from today’s non-200 later-page behavior (which keeps earlier pages and SUCCESS).

**Rejected for MVP:**
- **Partial success on Defer** — terminalize with pages already fetched (like HTTP failure keep-earlier); may permanently miss later pages that run.
- **Resume cursor** — requeue with page/POST-body state; higher complexity, revisit only if restart tax proves costly on high-`max_pages` API sources.

### D15 — FetchJob status `give_up` (distinct from `blocked`)

**Choice:** Add `FetchJob.Status.GIVE_UP = "give_up"` (“Gave up”) for D10 terminalize. Keep `blocked` for vendor block signals (403, blocked HTML/JSON body, etc.).

**Why:** UI and history already treat `blocked` as “vendor refused us.” Give-up means “we stopped waiting on rate limits / wall clock.” Reusing `blocked` would blur operator diagnosis. MVP UI: badge in fetch-job table (same pattern as other statuses); log `reason=give_up` remains required.

## Risks / Trade-offs

- **[Risk] Redis unavailable** → Fail closed / loud fallback for profiled sources; document Redis for multi-worker pacing + locks.
- **[Risk] Wrong Reset semantics** → Profile-specific parsers; unit tests; prefer IETF headers when present.
- **[Risk] Fair interval slow** → High per-run caps; env tunables; 50% headroom accepted for commercial safety.
- **[Risk] Stuck RUNNING without sweeper** → Mitigated by give-up + always-terminalize finally + unique FetchJob; no background sweeper by design.
- **[Risk] Overlapping WebUpdates** → Shared budget helps; full correctness still separate.
- **[Trade-off] Fan-out queue depth** → N tasks per run; accepted for utilization with tagged×overlapping sources.
- **[Trade-off] Dedup under concurrency** → Resolved by D12 (per-unit) + D11 (idempotent terminalize); overlapping WebUpdates still out of scope.
- **[Trade-off] No reserve refund (MVP)** → Failed send after Ready can leave phantom reserved cost until header reconcile or reset; accepted under 50% headroom; see D13 future note.
- **[Trade-off] Pagination Defer restarts page 1** → Re-spend early pages until give-up; accepted vs resume-cursor complexity (D14).
- **[Trade-off] Profile-gated urllib3 429** → HTML/`none` keeps opaque 429 retries; API profiles surface the first 429 for Defer (D6). Fetcher must select retry policy per send, not a single global `status_forcelist`.

## Migration Plan

1. Ship `rate_limit_profile` default none — HTML path equivalent per unit.
2. Replace monolithic `run_web_update_task` loop with fan-out + per-ItemSource tasks (keep progress UI fields).
3. Enable Redis budget + locks wherever Huey already uses Redis.
4. Operators set API profiles when ready.
5. Rollback: prior deploy; ephemeral Redis keys.

## Open Questions

*(none — planning decisions closed for this change.)*
