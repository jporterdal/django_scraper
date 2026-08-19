## Why

JSON and GraphQL price sources expose dynamic rate-limit budgets (headers and/or response extensions), but the app only applies a fixed delay plus opaque 429 retries. That cannot stay under commercial API plans safely when multiple Huey workers share a key, and it risks burning paid quota or triggering hard throttling. Typical runs also fan out many tagged items across overlapping sources inside one long-lived task, so sleeping a worker on a hot scope parks unrelated sources. We need shared, header-aware pacing and a smaller queue unit of work before more API sources come online.

## What Changes

- Add a pluggable **rate-limit awareness layer** for JSON/GraphQL (and related HTTP JSON) sources: extract budgets from responses, store them in a **shared Redis** budget store, and pace via atomic `try_acquire` (Lua check-and-reserve; short in-worker wait re-checks, else defer/requeue).
- **Redesign the scrape unit of work**: one Huey task per `ItemSource` (Option B), fan-out from schedule/manual update, with last-finisher CAS + FetchJob predicate for `WebUpdate` DONE (no sweeper).
- Support multiple **budget units** from day one: at least `request`, `cost` (GraphQL points), and `token`, applying the most constraining meter.
- Combine vendor remaining with **safety policy**: 50% headroom default, `min_interval`, **fair interval**, global per-run caps, **5s** short-wait threshold, give-up on defer, and idempotent Redis/Huey requeue.
- Honor `Retry-After` / 429 on **API-profiled** sources by deferring (not long-sleeping workers; not urllib3-retrying 429); HTML/`none` keeps today’s urllib3 429+503 retries. Fall back to fixed delay when meters are unknown.
- **Logging-only** MVP observability. UI indicators out of scope.
- Leave **plain HTML** sources on fixed-delay pacing inside each unit task (no dynamic budget profile).
- **Dedup under fan-out:** per-unit at terminalize (same predicates as today); no run-level finalize.
- **Pagination:** one ItemSource unit; `try_acquire` per page; Defer discards in-memory pages and restarts from page 1 (no resume cursor).
- **Out of scope**: full multi-worker scrape correctness beyond pacing; reserve-refund after failed send (future; see design D13); pagination resume cursor; UI for budgets; gRPC/SOAP.

## Capabilities

### New Capabilities

- `api-rate-limiting`: Extract, store, and enforce shared API budgets with atomic try_acquire; per-item-source Huey units with defer/give-up/idempotent requeue; DONE via last-finisher CAS + FetchJob predicate; per-unit ingest dedup at terminalize; multi-unit meters; structured logging.

### Modified Capabilities

- (none — no existing OpenSpec main specs yet)

## Impact

- **Code**: `tracking/tasks.py`, `tracking/scrape.py`, `tracking/fetcher.py`; new rate-limit modules; `Source.rate_limit_profile`; unique constraint on `FetchJob(webupdate, item, source)`; new `FetchJob` status `give_up`; WebUpdate counter updates via `F()` / CAS; per-unit dedup/store at terminalize. **Fetcher:** profile-gated urllib3 retries (429 only for HTML/`none`; 503 for all). **`scrape.py`:** profiled 429 no longer maps to `FetchOutcome.blocked` / `FetchJob.BLOCKED` — store cooldown + Defer/give-up instead; `(403, 429)→blocked` retained only for `none`/HTML.
- **Infra**: Redis for Huey, budget store (Lua check-and-reserve), and idempotent enqueue locks.
- **Config**: Global defaults (headroom 50%, min interval from scrape delay, fair interval on, 5s defer threshold, give-up caps, per-run caps).
- **Tests**: Offline; in-memory budget store with locked reserve; concurrent acquire tests; no live HTTP.
- **Non-goals this change**: Budget UI; sweeper-based DONE; reserve-refund on failed send; solving all overlapping-WebUpdate scrape races as a separate correctness project.
