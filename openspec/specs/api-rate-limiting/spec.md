# api-rate-limiting

## Purpose
Give JSON/GraphQL (and related HTTP JSON) price sources shared, header-aware rate-limit pacing across Huey workers, in place of fixed-delay-only pacing and opaque 429 retries — so commercial API plans and quotas are respected safely under a per-`ItemSource` unit-of-work fan-out, without a background sweeper or UI, while plain HTML sources keep today's fixed-delay behavior unchanged.

## Requirements

### Requirement: Rate-limit profiles for JSON API sources

The system SHALL associate each `Source` with a rate-limit profile key that selects how budgets are extracted and enforced. The default profile SHALL disable dynamic rate-limit awareness so existing HTML / non-API sources keep fixed-delay behavior only.

#### Scenario: Default profile leaves HTML behavior unchanged
- **WHEN** a Source uses the default (none/empty) rate-limit profile
- **THEN** requests for that Source SHALL be paced only with the existing fixed delay/jitter path and SHALL NOT require Redis budget state

#### Scenario: Operator selects an API profile
- **WHEN** a Source is configured with a registered API rate-limit profile (e.g. IETF headers, X-RateLimit, GraphQL cost)
- **THEN** the unit task for that Source SHALL use that profile's extractor and the shared pacer

### Requirement: Extract budgets from HTTP JSON responses

For a profiled Source, after an HTTP response the system SHALL extract zero or more budget meters from response headers and/or JSON body extensions according to the profile. Supported meter units from day one SHALL include at least `request`, `cost`, and `token`.

#### Scenario: REST RateLimit headers update request meter
- **WHEN** a profiled response includes recognizable remaining/limit/reset rate-limit headers for request counts
- **THEN** the system SHALL update the shared budget snapshot's `request` meter with remaining, limit, and reset time

#### Scenario: GraphQL cost extensions update cost meter
- **WHEN** a GraphQL profiled response includes cost/throttle information in JSON `extensions` (or profile-defined equivalent)
- **THEN** the system SHALL update the shared budget snapshot's `cost` meter from that information

#### Scenario: Missing meters fall back without inventing quota
- **WHEN** a profiled response does not include usable budget signals
- **THEN** the system SHALL leave remaining quota as unknown and SHALL pace using the fixed-delay fallback rather than inventing a remaining value

### Requirement: Shared budget store across workers

The system SHALL persist budget snapshots in a shared store keyed by rate-limit scope so multiple Huey workers observe the same remaining/reset state. In production with Redis configured, the store SHALL use Redis. Under test settings, the system SHALL provide an in-memory (or equivalent) store so tests run without Redis or network.

#### Scenario: Two workers share remaining quota
- **WHEN** two workers handle requests for the same rate-limit scope
- **THEN** both SHALL read and write the same shared budget snapshot for that scope

#### Scenario: Concurrent acquire reserves at most once
- **WHEN** two workers call `try_acquire` for the same scope and usable remaining allows only one estimated cost
- **THEN** at most one SHALL receive Ready with a reservation; the other SHALL receive ShortWait or Defer without both sending

#### Scenario: Tests do not require Redis
- **WHEN** tests exercise rate-limit pacing and extraction
- **THEN** they SHALL pass offline using the test/fake budget store without a live Redis server

### Requirement: Atomic try_acquire check-and-reserve

Before sending a profiled request, the system SHALL decide Ready / ShortWait / Defer in an atomic check-and-reserve against the shared store (Redis Lua in production; equivalent locked logic in the in-memory test store). Ready SHALL reserve the estimated cost and claim the next interval slot; ShortWait and Defer SHALL NOT reserve. After ShortWait the caller SHALL re-invoke `try_acquire` rather than sending without a fresh Ready. The system SHALL NOT require refunding a reservation when a send fails before a useful response in this change (future enhancement).

#### Scenario: Short wait re-checks before send
- **WHEN** `try_acquire` returns ShortWait
- **THEN** the worker SHALL sleep for the wait duration and call `try_acquire` again before any HTTP send

#### Scenario: Ready reserves before send
- **WHEN** `try_acquire` returns Ready
- **THEN** the shared store SHALL already reflect the reserved estimated cost (and updated next-allowed time) before the HTTP request is sent

### Requirement: Pace with headroom, fair interval, and short-wait threshold

Before sending a request for a profiled Source, the system SHALL compute usable remaining as half of vendor remaining by default (`headroom_pct = 0.50` unless globally overridden). The system SHALL compute an effective wait from minimum interval and fair interval (when reset is known). Waits strictly below **5 seconds** SHALL be slept in-worker; waits of **5 seconds or more**, exhausted usable until reset, and 429 `Retry-After` cooldowns SHALL **Defer** (requeue) rather than long-sleep the worker. When multiple meters apply, the system SHALL allow the request only if every applicable meter has sufficient usable remaining for the estimated cost of the call.

#### Scenario: Headroom reserves half of remaining
- **WHEN** vendor remaining for a meter is 100 and headroom is 50%
- **THEN** usable remaining for pacing SHALL be 50

#### Scenario: Fair interval smooths drain
- **WHEN** usable remaining is 50 and reset is 100 seconds in the future
- **THEN** the pacer SHALL compute a fair interval of approximately 2 seconds per request (subject to min interval)

#### Scenario: Short wait sleeps in-worker
- **WHEN** effective wait is 2 seconds
- **THEN** the worker SHALL sleep approximately 2 seconds and re-invoke `try_acquire` without requeueing (and SHALL NOT send until a later Ready)

#### Scenario: Long wait defers
- **WHEN** effective wait is 5 seconds or greater
- **THEN** the unit task SHALL requeue with an eta and return without holding the worker for the full wait

#### Scenario: Most constraining meter wins
- **WHEN** usable request remaining allows a call but usable cost remaining is below the estimated query cost
- **THEN** the system SHALL ShortWait or Defer until cost usable remaining is sufficient (per threshold rules)

### Requirement: Per-ItemSource Huey unit of work

A scheduled or manual web update SHALL create one `WebUpdate` with `total_searches` equal to the number of planned item-sources and SHALL enqueue one Huey task per `ItemSource`. Each task SHALL perform at most that pair's fetch work (including pagination for that pair) and SHALL either terminalize or Defer.

#### Scenario: Fan-out enqueues N tasks
- **WHEN** a run plans 12 item-sources
- **THEN** the system SHALL enqueue 12 unit tasks for that `WebUpdate` and set `total_searches` to 12

#### Scenario: Deferred unit frees the worker
- **WHEN** a unit task Defers due to rate limits
- **THEN** that worker SHALL become available for other tasks without having written a FetchJob for the deferred pair

#### Scenario: Multi-page unit paces each page
- **WHEN** a profiled Source has `max_pages` greater than 1
- **THEN** the unit task SHALL call `try_acquire` before each page request and SHALL only Defer at page boundaries

#### Scenario: Defer mid-pagination restarts from page 1
- **WHEN** a unit Defers after one or more pages in the same attempt already succeeded
- **THEN** the system SHALL discard that attempt's in-memory page results, requeue the unit, and on the next attempt start again from page 1 (no mid-pagination resume cursor)

### Requirement: DONE via last-finisher CAS and FetchJob predicate

The system SHALL ensure at most one terminal `FetchJob` per `(webupdate, item, source)`. Defer SHALL NOT create a FetchJob or increment `completed_searches`. On terminalize, the system SHALL record the FetchJob, atomically update progress counters, and when the number of FetchJobs for the `WebUpdate` reaches `total_searches`, compare-and-set status from `running` to `done`. The system SHALL NOT rely on a background sweeper to mark DONE. Single-unit failures SHALL terminalize as failed/empty/blocked jobs and SHALL NOT mark the whole `WebUpdate` as `failed` unless fan-out/orchestration itself fails.

#### Scenario: Last unit marks DONE
- **WHEN** the Nth terminal FetchJob for a WebUpdate with `total_searches = N` is recorded
- **THEN** exactly one worker SHALL successfully transition that WebUpdate to `done`

#### Scenario: Defer does not advance completion
- **WHEN** a unit Defers
- **THEN** `completed_searches` SHALL be unchanged and no FetchJob SHALL exist yet for that pair

#### Scenario: Duplicate delivery does not double-count
- **WHEN** a unit task runs again after a FetchJob for that pair already exists
- **THEN** the system SHALL no-op without incrementing counters again

### Requirement: Give-up closes the barrier without a sweeper

Before requeueing a Defer, the system SHALL give up when `attempt >= 5` or when the WebUpdate age is at least 30 minutes. Give-up SHALL terminalize with FetchJob status `give_up` (distinct from `blocked`), increment completion, log `reason=give_up`, and participate in DONE detection like any other terminal outcome. The system SHALL NOT reuse `blocked` for give-up; `blocked` remains for vendor block responses (e.g. HTTP 403 / blocked body).

#### Scenario: Max defer attempts
- **WHEN** a unit is about to Defer and `attempt` is already 5 or greater
- **THEN** the system SHALL terminalize with status `give_up` instead of requeueing

#### Scenario: Wall-clock give-up
- **WHEN** a unit is about to Defer and the WebUpdate is 30 minutes old or older
- **THEN** the system SHALL terminalize with status `give_up` instead of requeueing

#### Scenario: Give-up is not blocked
- **WHEN** a unit give-up terminalizes because attempts or wall clock were exceeded without a vendor block response
- **THEN** the FetchJob status SHALL be `give_up` and SHALL NOT be `blocked`

### Requirement: Idempotent enqueue and requeue

The system SHALL use a Redis `SET NX` lock per `(webupdate_id, item_source_id)` so duplicate enqueue/requeue does not schedule concurrent duplicate work. Lock TTL SHALL be at least the time until eta plus 120 seconds grace (with a minimum TTL of 300 seconds for immediate runs). The system SHALL enforce a database uniqueness constraint on `FetchJob(webupdate, item, source)` so terminalize is idempotent.

#### Scenario: Duplicate enqueue skipped
- **WHEN** a lock already exists for a unit key
- **THEN** a second enqueue attempt SHALL skip scheduling another task

#### Scenario: Requeue replaces lock
- **WHEN** a unit Defers
- **THEN** the system SHALL release or replace the lock and schedule at most one follow-up task for that unit

### Requirement: Honor hard limits and per-run caps

On HTTP 429 for an **API-profiled** Source, the system SHALL honor `Retry-After` when present by treating the scope as exhausted until that time in the shared store and Deferring. urllib3 SHALL NOT retry 429 for profiled sends (503-only backstop). Default/`none` (HTML) Sources SHALL keep urllib3 retry of 429 and 503 as today. The system SHALL enforce global per-run caps so a single web update cannot consume more than the configured maximum usable units even if vendor remaining is higher.

#### Scenario: 429 with Retry-After defers
- **WHEN** a profiled request receives HTTP 429 with `Retry-After` of 30 seconds
- **THEN** the shared store SHALL reflect no usable remaining until that cooldown and the unit SHALL Defer (not sleep 30 seconds in-worker)

#### Scenario: Profiled 429 is not urllib3-retried
- **WHEN** an API-profiled request receives HTTP 429
- **THEN** the Fetcher SHALL NOT retry that 429 via urllib3; the unit SHALL apply shared-store cooldown and Defer (or give-up)

#### Scenario: HTML/none keeps urllib3 429 retry
- **WHEN** a default/`none` profile request receives HTTP 429
- **THEN** urllib3 MAY retry 429 as today; if still non-200 after retries, existing blocked/http_error classification applies

#### Scenario: Profiled 429 is not FetchJob blocked
- **WHEN** a profiled unit handles HTTP 429
- **THEN** it SHALL NOT terminalize with `blocked` solely because the status was 429; Defer or give-up SHALL apply instead (`blocked` remains for 403 / blocked-body signals)

#### Scenario: Per-run cap stops further calls
- **WHEN** a web update has already consumed the global max requests (or cost) for rate-limited calls in that run
- **THEN** further profiled units for that run SHALL not send requests that would exceed the cap and SHALL log the cap reason (terminalize or skip per implementation, without leaving the barrier unable to close)

### Requirement: Structured logging without UI

The system SHALL emit structured logs describing budget updates, pace decisions, deferrals, and give-ups. The system SHALL NOT require UI surfaces for rate-limit status in this change.

#### Scenario: Pace or defer decision is logged
- **WHEN** the pacer sleeps, defers, or gives up
- **THEN** a log entry SHALL include the reason and relevant timing (sleep_ms, eta, or give_up)

#### Scenario: No UI requirement
- **WHEN** this capability is implemented
- **THEN** operators MAY rely on logs alone; absence of UI indicators SHALL NOT be treated as incomplete for MVP

### Requirement: Per-unit ingest dedup at terminalize

When a unit task terminalizes successfully with parsed candidates, the system SHALL deduplicate those candidates before persisting `SearchResult` rows, using the same predicates as today's run-level path: key `(item, source, title)` compared on `(price, instock)` against prior-run snapshots (excluding the current `WebUpdate`) and against duplicates within that unit's candidate list. The system SHALL NOT perform a run-level finalize or candidate-staging reduce. Cross-unit within-run collision SHALL NOT require special handling because each unit owns a unique `(item, source)` pair. The system SHALL set `FetchJob.stored_count` and atomically update `WebUpdate.result_count` / `skipped_duplicate_count` from the unit's dedup outcome. Duplicate delivery of the same unit SHALL NOT store again (FetchJob uniqueness / idempotent terminalize).

#### Scenario: Unchanged vs prior run stores nothing
- **WHEN** a unit parses results whose `(title, price, instock)` match the latest prior snapshot for that item and source
- **THEN** the system SHALL store no new `SearchResult` rows for those candidates, set `stored_count` to the number actually inserted (zero if all unchanged), and increment the WebUpdate skipped-duplicate counter accordingly

#### Scenario: Within-unit duplicate titles store once
- **WHEN** a unit's candidate list contains two identical `(title, price, instock)` rows for the same item and source
- **THEN** the system SHALL persist at most one `SearchResult` for that key and count the other as skipped

#### Scenario: No run-level finalize
- **WHEN** the last unit of a WebUpdate terminalizes and marks the run DONE
- **THEN** all surviving `SearchResult` rows for that run SHALL already have been persisted by unit terminalize; the system SHALL NOT require a post-DONE reduce step
