## Context

The app is single-tenant with no per-user ownership: no model has a user foreign key, and `LoginRequiredMiddleware` in `MIDDLEWARE` is the only gate. Everything a visitor can trigger runs against one shared database. Facts that shape the approach (see proposal.md for motivation):

- **Change-only storage.** `_dedupe_unit_candidates` skips a row when `(price, instock)` matches the newest stored row for the `(item, source, title)` thread. A stable listing is therefore one old row, and `SearchResult` has no timestamp of its own — its age is its `WebUpdate.timestamp`. `SearchResult.update` is `on_delete=CASCADE`, so deleting an old run would silently delete a current price.
- **Single fan-out entry.** Manual runs (`UpdateFromWebView`) and scheduled runs (`dispatch_due_schedules`) both call `dispatch_fan_out`, which creates the `WebUpdate` and enqueues one `fetch_one` per `ItemSource`. `terminalize` records exactly one `FetchJob` per unit and closes the run when the `FetchJob` count reaches `total_searches`, so a unit that terminalizes as "throttled" closes the run through the existing barrier.
- **Free-form fetch inputs.** `Source.base_search_url`/`request_headers`/`request_body_template` and `ItemSource.pinned_url`/`url_suffix` reach `requests` with no host, scheme, or address validation; `title_include_patterns`/`title_exclude_patterns` reach `re.search` and are re-evaluated at read time.
- **No item deletion route.** Items are deactivated, never deleted, outside the Django admin (`SearchableItem` is registered there). Items never expire in demo mode (proposal), so slots are freed only by operator cleanup in the admin.
- **Conventions in force** (`spec/agentrules.md`): the suite runs with no network, Redis, or Postgres; no new unpinned dependencies; no vendor names in committed app code; periodic tasks are a pure function plus a thin `@periodic_task` wrapper (`dispatch_due_schedules` / `dispatch_scheduled_updates`); tests live in `tracking/tests/test_<topic>.py`; migrations chain off `0022_expected_value_vendor_provenance`.

## Goals / Non-Goals

**Goals:**
- Private-by-default stays true: with `DEMO_MODE` off the app behaves exactly as today, and a new route added later is private until someone classifies it.
- Every limit works on its own, so the mechanisms are testable and reusable without demo mode.
- A public visitor can never cause the server to fetch a URL or evaluate a pattern that an operator did not provision.

**Non-Goals:**
- Per-visitor ownership, accounts, or sandboxes (cookie-based ownership is documented as a future option only).
- Login throttling and closing `/admin/login/` (adjacent hardening that also applies to the private deployment; separate follow-ups).
- Item expiry or auto-cleanup, seed data, an item-delete view, replaying recorded vendor responses.
- Network-level SSRF defense in the fetcher (address/redirect filtering). The controls here remove visitor influence over fetch targets; they do not make operator-provisioned sources hostile-proof.

## Decisions

### D1. One central access table plus a `LoginRequiredMiddleware` subclass, not per-view decorators

A new module owns a table mapping each application URL name to one of `public_read`, `public_write`, or `login_only`. `settings.MIDDLEWARE` swaps Django's `LoginRequiredMiddleware` for a subclass whose `process_view` keeps the parent behavior (including honoring `login_required = False` on framework views such as the login page) and adds one branch: when `settings.DEMO_MODE` is true and the user is anonymous, look up the resolved URL name and admit `public_read` for safe methods (GET/HEAD/OPTIONS) and `public_write` for any method; otherwise fall through to the parent redirect. Unknown names, unnamed routes, and namespaced framework routes fall through, so the default is login-only. With `DEMO_MODE` off the extra branch never fires.

A test walks the project URLconf, collects every `tracking` route name, and asserts the set equals the table's keys — a new route without a classification fails the suite (the "fail-closed as the app grows" guarantee). A second test asserts anonymous and operator outcomes per tier.

*Alternative — decorate views (`login_not_required`-style marker).* Rejected: policy scatters across a 1,300-line `views.py`, and "forgot to decorate" is indistinguishable from "intentionally private", so the completeness test cannot be written. Django's decorator is also unconditional, while prod must stay fully private.

*Tier admits the request; the view narrows it.* `public_write` routes that need finer rules enforce them themselves (D3, D4). The rule "anonymous" is `not request.user.is_authenticated`, which only occurs in demo mode, so views need no `DEMO_MODE` checks of their own.

### D2. Limits are generic settings; `DEMO_MODE` only supplies defaults; everything is read at call time

Settings (env names): `MAX_ITEMS`, `HISTORY_RETENTION_DAYS`, `PER_FETCH_MAX_ROWS`, `FETCH_COOLDOWN_MINUTES`, `FETCH_BUDGET_PER_DAY`, `MAX_SEARCH_RESULT_ROWS`, and the existing `SCRAPE_MAX_RESPONSE_BYTES`. Each is read as `env.int(NAME, default=<demo value> if DEMO_MODE else <off value>)`, where `0` means unlimited (for the response cap, the current default). Modules read `django.conf.settings` when called, never at import, so `override_settings` works in tests.

Initial demo defaults (tunable; the two the maintainer fixed are marked):

| Setting | Demo default | Sizing note |
|---|---|---|
| `MAX_ITEMS` | 25 | 25 items × ~3 sources = 75 units per full pass |
| `HISTORY_RETENTION_DAYS` | **14** | fixed by maintainer |
| `PER_FETCH_MAX_ROWS` | **100** | fixed by maintainer; smoke test of one 30-product page yielded 190 variant rows |
| `FETCH_COOLDOWN_MINUTES` | 60 | |
| `FETCH_BUDGET_PER_DAY` | 300 | ≈ one full pass per schedule run plus visitor headroom |
| `MAX_SEARCH_RESULT_ROWS` | 50,000 | far above 25 × 3 × 100 = 7,500 first-fill rows; a backstop |
| `SCRAPE_MAX_RESPONSE_BYTES` | 3,000,000 | ~1.6× the observed 1.8 MB page (was 8 MB) |

The test settings must pin these: because `settings_test` does `from .settings import *`, it explicitly sets `DEMO_MODE = False` and each limit to `0`, so a developer's `.env` cannot change suite behavior.

### D3. Anonymous inputs are narrowed at the form layer by removing fields

Forms take an `operator` flag derived from the request. For anonymous visitors the item-source form drops `url_suffix`, `title_include_patterns`, and `title_exclude_patterns` entirely (removed from the form, so posted values are ignored — not merely hidden), restricts `source` to existing sources, and validates `pinned_url` as `http`/`https` with a host equal to the chosen source's `base_search_url` host (case-insensitive). The same form class backs the bulk-add formset, so bulk add inherits this. Search-term validation for anonymous visitors uses a whitelist (`ch.isalnum()` or one of `' - . , : & ! ( ) / +` or a space) on top of the existing 125-character limit, which permits accented card names while excluding markup and control characters. The item form is otherwise unchanged: expected product-line/category values are matched with `re.escape`, so they are not a pattern surface.

*Alternative — validate a pinned URL against an env allowlist of hosts.* Rejected: the curated sources already define the allowed hosts, so the allowlist would be a second copy to keep in sync.

### D4. Manual scrape gating lives in `UpdateFromWebView`

`mode=all` and `mode=tag` require login for anonymous visitors (flash message pointing to the operator login, no run created). `mode=selected` for anonymous requires exactly one id. Everything downstream is identical, so limit enforcement (D5) applies uniformly.

### D5. Scrape limits: admit at `dispatch_fan_out`, re-check at execution, record throttled units through `terminalize`

`dispatch_fan_out` computes its planned unit list, then applies admission before enqueueing: (1) units belonging to an item still in cooldown are throttled; (2) of the rest, only as many as the remaining daily budget proceed, in `ItemSource` id order; every throttled unit is recorded synchronously through `terminalize(status=THROTTLED)`. Because this happens inside the web request, the view can query the run's throttled jobs immediately and tell the visitor how many were skipped and why. `fetch_one_unit` repeats the same admission check immediately before the request, so queued work cannot outrun a budget spent by another run, and a duplicate concurrent run cannot bypass the cooldown; with two worker threads the possible overshoot is at most a couple of fetches, which is acceptable for a cost cap.

Both limits derive from existing tables, with no new state: a unit "sent a request" if its `FetchJob` status is not `throttled` and not `config_error`; its time is its `WebUpdate.timestamp` (the run start; `FetchJob` has no timestamp of its own). The budget counts such jobs in the trailing 24 hours (`timestamp` is already indexed). The cooldown compares the newest such job for the item. Throttled jobs are excluded from both counts, so throttling never extends a cooldown or eats budget.

*Alternative — Redis counters.* Rejected: derived counts survive restarts, work without Redis in tests, and cannot drift from what actually happened.

### D6. Per-fetch cap applied in `terminalize` before deduplication

`terminalize` already receives the filtered candidate list, so the cap slices `candidates[:PER_FETCH_MAX_ROWS]` there, before `_dedupe_unit_candidates`. The discarded count is stored as a new `FetchJob.truncated_count`. `result_count` keeps its meaning (post-filter, pre-cap, pre-dedup), and the `skipped_count` property becomes `result_count - truncated_count - stored_count` so truncation is not misreported as an unchanged duplicate. Truncation is flat: parsers emit rows with no product identity (title is `"Name (Condition)"`), and adding grouping to every parser would be disproportionate, so the last product may be left with only some of its conditions. Because parsers emit in vendor relevance order and the cap keeps a prefix, an identical response selects an identical set, and dedupe then stores nothing new.

### D7. Item cap is enforced in `SearchableItem.save()` with a lock on PostgreSQL, and pre-checked in forms

When adding, `save()` runs in `transaction.atomic()`, takes `pg_advisory_xact_lock` with a fixed key when `connection.vendor == "postgresql"`, counts all items, and raises a dedicated `ItemLimitExceeded` when the cap is reached. Forms pre-check remaining slots so visitors get a validation message and not an exception; bulk add rejects the whole submission when its term count exceeds remaining slots, and wraps creation in one transaction so a lost race rolls back everything. Every creation path already goes through `save()` (`ModelForm.save()` and `SearchableItem.objects.create`; `bulk_create` is not used for items).

*Alternative — insert then count, roll back if over.* Rejected: under PostgreSQL's default READ COMMITTED, two concurrent transactions each see only committed rows plus their own, so both can pass the post-insert check. The advisory lock serializes the count-then-insert. SQLite serializes writers itself and is dev/test only, so the race test is limited to asserting the lock is taken on PostgreSQL; the concurrent case is verified manually against PostgreSQL (task 11.3).

### D8. `FetchJob` schema: two statuses and one column, one migration

`FetchJob.Status` gains `THROTTLED` and `STORAGE_FULL`; `FetchJob` gains `truncated_count` (small positive integer, default 0). One migration numbered `0023`, chaining off `0022_expected_value_vendor_provenance`, additive and safely reversible. Throttled units store their human reason (`cooldown` or `daily budget`) in the existing `error_message` (operator-visible; anonymous views show only the status label, per the hiding rule). `is_error` in `terminalize` stays false for both new statuses, so they don't inflate `WebUpdate.error_count`.

### D9. Retention is pure functions on age-by-run, deleting rows directly and never through `WebUpdate`

A new retention module exposes pure functions called by a thin daily `@periodic_task` wrapper in `tasks.py` (the `dispatch_due_schedules` pattern):

1. `prune_results(cutoff)`: rank rows older than the cutoff per `(item, source, title)` by `(update__timestamp desc, id desc)` with a window function; rows ranked 1 are anchors and stay, the rest are deleted in id batches (so lock time stays short and the window-filtered query never feeds `.delete()` directly).
2. `prune_runs(cutoff)`: delete terminal `WebUpdate`s older than the cutoff that have no `SearchResult` (an `Exists` subquery); `FetchJob`s cascade with them. A run referenced by an anchor stays with its jobs.
3. Delete terminal (non-pending) `MetadataFetchRequest`s older than the cutoff. `ObservedCategoryValue` is not pruned: it has no timestamp and is bounded by its unique `(source, field_name, value)` constraint.

*Alternative — make `SearchResult.update` nullable/`SET_NULL`.* Rejected: read paths order and filter by `update__timestamp` (dedupe snapshot, latest-price, charts), so the FK is load-bearing; pruning only unreferenced runs keeps the schema and read paths untouched.

This deletes rows by age. It is not the row-exclusion of `retroactive-result-matching`, whose "does not delete" rule concerns re-evaluating relevance; retention decides on age alone and ignores relevance.

### D10. Row ceiling: check inside `terminalize`, evict in a defined order, otherwise refuse

Before inserting survivors, `terminalize` calls a capacity check with the incoming count. If `count + incoming` fits, insert. If not, it first runs the retention prune (cheap when nothing is due), then evicts the oldest evictable rows. Evictable rows are those that are not the newest row of their thread and not older than the cutoff (after pruning, rows older than the cutoff are exactly the anchors, so this protects anchors; with no window configured the second clause is vacuous). If it still does not fit, the unit records `STORAGE_FULL`, inserts nothing, and leaves existing rows alone. The status must be decided before `_record_fetch_job`, so the check precedes that call. A row count over at most ~50,000 rows is a millisecond-scale query.

### D11. Templates branch on `user.is_authenticated`, not on the flag

Anonymous users exist only in demo mode, so `base.html` and list/detail/partial templates check `user.is_authenticated` (and `user.is_staff` for the admin link). This needs no new context and keeps private-deployment markup unchanged. The sources list and fetch-job partial omit connection details for anonymous users (`base_search_url`, `request_headers`, `request_body_template`, `FetchJob.search_url`, `FetchJob.error_message`); an implementation audit greps templates for these fields, since hiding them in two known places is not proof they appear nowhere else. A small context processor adds the banner data (item slots, remaining fetches, retention days) only when `DEMO_MODE` is on, at two cheap queries per page.

### D12. The `DEBUG` guard lives in `AppConfig.ready()`, not in settings

The rule "refuse to start when both are on" is checked in the app's `ready()` and raises `ImproperlyConfigured`. It is not a system check (gunicorn never runs system checks) and not in `settings.py` (a `.env` with both true would make `from .settings import *` in `settings_test` raise before its overrides apply, breaking the suite for that developer). `ready()` runs on every entry point, against final settings, and logs the resolved `DEMO_MODE` at startup so a web/worker mismatch is visible in logs.

### D13. Metadata: public retry and candidate selection, login-only manual identifier

Retry and candidate selection are `public_write`; manual external-id entry is `login_only`. The scheduler-not-firing warnings need no suppression: with the prod-parity stack (Redis configured, Huey not immediate) `schedules_may_not_fire()` is false and the warning stays truthful. Metadata volume is bounded by the item cap and the existing bounded-rate drain.

### D14. Exports are not separately capped

The earlier idea of a dedicated export cap is dropped: an item's exported rows are already bounded by the per-fetch cap, the retention window, and the global ceiling.

### D15. Ordering with `streamline-view-terms-navigation`

That in-flight change (0/23 tasks) regroups the nav into `Add ▾`/`Manage ▾`, moves Admin to a top-level link, and merges the two update buttons into one control on `view_terms`. This change makes exactly those controls role-aware. The tasks below are written against the post-streamline markup; apply that change first. If the order flips, the same conditionals apply to the current markup and the two changes need a small manual reconciliation in `base.html` and `searchableitem_list.html`.

## Risks / Trade-offs

- **A shared, ownerless dataset invites junk** → item cap, term whitelist, no anonymous edit/delete, operator cleanup via the admin; the runbook documents cleanup. The demo can be filled by one visitor and stay full until cleaned (accepted: no expiry by decision).
- **One visitor can exhaust the daily budget** → everyone sees throttled outcomes until it recovers; the banner shows the remaining budget so the state is legible.
- **Truncated tails go stale.** A title that falls out of the 100-row prefix stops receiving new rows, and its last row stays as its "latest known" price (the same pre-existing behavior as a delisted listing, made more likely by the cap) → documented; consider a "not seen recently" indicator later.
- **A cooldown can throttle a schedule that fires faster than the cooldown.** The cooldown compares run start times, so an hourly schedule with a 60-minute cooldown can skip itself → docs tell the operator to keep the cooldown below the shortest schedule interval.
- **Job time is approximated by run start** (`FetchJob` has no timestamp) → a unit deferred for a long time counts by its run's start; the error is small for a 24-hour window.
- **The budget and cooldown re-check is not perfectly atomic** → overshoot bounded by concurrent workers (2 threads).
- **Public write endpoints have no ownership or rate limit per visitor** → bounded by the item cap and daily budget; per-IP throttling and login throttling are follow-ups.
- **Web and worker must share env.** Retention and unit-level checks run in the worker; if the worker lacks `DEMO_MODE`/limit vars its behavior diverges → the startup log line and the deploy docs call this out.
- **Authenticated ≠ operator.** Any authenticated user sees login-only screens; only staff see the admin. No signup exists (`createsuperuser` only), so this holds today → documented; revisit if accounts are ever added.
- **Sources have connection details visible to the operator only if the templates are audited thoroughly** → the audit task greps for every sensitive field.

## Migration Plan

1. Apply `streamline-view-terms-navigation` first (D15).
2. Ship the code with `DEMO_MODE` unset: the migration is additive, behavior is unchanged, and the prod deployment can adopt it independently.
3. Create the demo environment as a separate project environment with its own Postgres and Redis; set the same env on web and worker (`DEMO_MODE=True`, `DEBUG=False`, `SECURE_DEPLOYMENT=True`, host/CSRF settings, distinct `SECRET_KEY`, operator credentials different from prod).
4. Deploy, run migrations, create the operator account, log in, provision sources, create sample items, and verify the anonymous experience in a private window.
5. Set the platform spending limit as a last-line backstop.

Rollback: set `DEMO_MODE` off (the site becomes fully private again) or unset individual limits to remove them; the migration adds only a column and choices, so no data migration is needed to roll back the app code.

## Open Questions

- Final tuned values for the non-fixed defaults (cooldown, budget, ceiling, response cap) — adjustable through env vars without code changes once real usage is observed.
- Whether to add a per-item "Update this item" button on the item detail page for anonymous visitors, versus relying on selecting one row in the list and using the existing update control. Either satisfies the spec; the first is a UX nicety that can follow.
