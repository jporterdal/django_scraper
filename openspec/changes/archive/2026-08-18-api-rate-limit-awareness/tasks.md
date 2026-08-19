## 1. Settings and Source profile hook

- [x] 1.1 Add global rate-limit settings: `headroom_pct=0.50`, min_interval (default from `SCRAPE_REQUEST_DELAY_SECONDS`), fair_interval on, `short_wait_threshold=5s`, `max_requests_per_run=10000`, `max_cost_per_run=100000`, `MAX_DEFER_ATTEMPTS=5`, `MAX_RUN_WALL_CLOCK=30m`; document in `.env_sample`
- [x] 1.2 Add `rate_limit_profile` on `Source` (default none/empty); migration + admin/form as simple choice/text
- [x] 1.3 Register profile keys (`none`, `ietf`, `x-ratelimit`, `graphql_cost` at minimum) parallel to parser registry

## 2. Budget model, store, and extractors

- [x] 2.1 Implement budget snapshot + meters (`request`, `cost`, `token`) with remaining/limit/reset
- [x] 2.2 Implement BudgetStore with in-memory (tests) and Redis (`REDIS_URL`) backends
- [x] 2.3 Implement IETF / `X-RateLimit-*` extractors with tests for reset epoch vs delta
- [x] 2.4 Implement GraphQL cost/throttle extension extractor with fixture JSON tests (learn cost after first response; conservative default before)
- [x] 2.5 On **profiled** 429 + `Retry-After`, zero usable in shared store until cooldown (feeds Defer, not long sleep); profiled sends use urllib3 `status_forcelist` **without** 429 (503 only); `none`/HTML keeps 429+503 opaque retries (D6)

## 3. Pacer (`try_acquire`)

- [x] 3.1 Usable remaining = `floor(remaining * (1 - headroom_pct))` with default 50% headroom
- [x] 3.2 Min interval + fair interval; effective wait = max of both when reset known
- [x] 3.3 Multi-meter gating (most constraining wins)
- [x] 3.4 Map wait to Ready | ShortWait(&lt;5s) | Defer(eta); Retry-After / exhausted usable → Defer; ShortWait caller sleeps then **re-invokes** `try_acquire` (never send without re-check)
- [x] 3.5 Atomic check-and-reserve: Redis Lua script (mutex-shaped semantics); in-memory store uses a lock; Ready reserves estimated cost, bumps `next_allowed_at`, INCRs per-run caps; ShortWait/Defer mutate nothing
- [x] 3.6 Per-run request/cost caps with structured log reason; ensure capped units still terminalize so DONE can close
- [x] 3.7 Structured logs for budget updates and pace/defer/give_up decisions
- [x] 3.8 Tests: two concurrent acquires with usable allowing only one Ready; ShortWait re-acquire path; no reserve-refund required for MVP

## 4. Fan-out unit of work (Option B)

- [x] 4.1 Replace monolithic run loop enqueue with: create `WebUpdate(PENDING, total_searches=N)` + enqueue one task per `ItemSource`
- [x] 4.2 Implement `fetch_one(webupdate_id, item_source_id, attempt=0)` performing build URL/headers/body → pacer → `_run_parser_search` (or HTML wait path) for that pair only
- [x] 4.3 Wire schedule dispatch and manual update view to the fan-out path; preserve progress UI fields (`completed_searches`, etc.)

## 5. DONE, give-up, idempotent requeue

- [x] 5.1 Add unique constraint on `FetchJob(webupdate, item, source)` (+ migration)
- [x] 5.2 Implement `terminalize`: insert FetchJob, `F()` counters, PENDING→RUNNING, CAS RUNNING→DONE when FetchJob count ≥ `total_searches`
- [x] 5.3 Defer path: no FetchJob / no counter bump; give-up when `attempt >= 5` or WebUpdate age ≥ 30m → terminalize with `FetchJob.Status.GIVE_UP` + log `reason=give_up` (do not reuse `blocked`)
- [x] 5.4 Add `give_up` to `FetchJob.Status` (+ migration); show badge in `_webupdate_fetch_jobs.html`
- [x] 5.5 Redis `SET NX` lock per `fetchone:{webupdate}:{item_source}` with TTL = max(eta−now,0)+120s (min 300s); skip duplicate enqueue; replace lock on Defer; delete on terminalize
- [x] 5.6 Unit start: if FetchJob already exists for pair, no-op (idempotent); try/finally always terminalizes on unexpected errors
- [x] 5.7 Reserve `WebUpdate.FAILED` for fan-out/orchestrator failure only

## 6. Scrape path behavior

- [x] 6.1 Profiled sources: try_acquire → request → extract → store; `none`/HTML: fixed delay only
- [x] 6.2 Cold-start / unknown meters: fixed-delay fallback without inventing remaining
- [x] 6.3 Rewrite `scrape.py` 429 classification (D6): for profiled units, page‑1 HTTP 429 MUST NOT set `FetchOutcome.blocked` or map to `FetchJob.BLOCKED` — route to store cooldown + Defer/give-up instead; keep `(403, 429)→blocked` only for `none`/HTML (after urllib3 retries); keep 403 + blocked-body → `blocked` for all profiles
- [x] 6.4 Pagination (D14): `try_acquire` before each page for profiled sources; Defer only at page boundaries; on Defer discard in-memory pages and restart from page 1 (no resume cursor); HTML/`none` keep fixed delay between pages
- [x] 6.5 Confirm existing HTML-oriented tests still pass under fan-out + default profile (adjust tests that assumed one task / inline loop as needed)

## 7. Tests and docs

- [x] 7.1 Tests: headroom, fair interval, 5s threshold (sleep+re-acquire vs defer), multi-meter, Retry-After defer, profiled 429 not urllib3-retried / not `blocked`, HTML/`none` keeps urllib3 429 retry, per-run cap, give-up, CAS DONE, duplicate enqueue/terminalize idempotency, concurrent try_acquire, pagination Defer restarts page 1, in-memory store
- [x] 7.2 Run full suite: `python manage.py test tracking --settings=django_scraper.settings_test` → OK
- [x] 7.3 Document operator notes (profiles, Redis for budgets+locks, fan-out, global env knobs) in README or `docs/`

## 8. Per-unit ingest dedup (Option B)

- [x] 8.1 Move `_deduplicate_result_kwargs` / snapshot logic into the unit terminalize path (same key `(item, source, title)`, compare `(price, instock)`)
- [x] 8.2 Scope snapshot query to the unit’s item (optionally also `source_id`); drop the run-level batch snapshot map
- [x] 8.3 Persist survivors in-unit; set `FetchJob.stored_count`; `F()` bump `WebUpdate.result_count` and `skipped_duplicate_count` (progress reflects stored/skipped only, not pre-dedup)
- [x] 8.4 Keep existing dedup tests green under fan-out (identical second run → 0 stored / skipped counters; within-unit duplicate titles; changed price stores)