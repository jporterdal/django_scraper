## Why

The app is a private, single-tenant tool: every route sits behind `LoginRequiredMiddleware`, and nothing bounds how much data it stores, how many items it tracks, or how often it fetches. That makes it impossible to show as a live portfolio piece — a public URL would need either an open door to a server that fetches arbitrary URLs and evaluates visitor-supplied regexes, or a login wall nobody can pass. The goal is a `DEMO_MODE` deployment that is cheap to host and safe to expose, without forking the codebase or weakening the private-by-default posture of the real deployment.

## What Changes

- Add a `DEMO_MODE` environment flag (read like `DEBUG`) that, when on, lets anonymous visitors use a curated subset of the app while everything else stays behind login. With the flag off (the default) behavior is unchanged: every route still requires login.
- Introduce a **two-tier, fail-closed access model**. A single central table classifies every route as public-read, public-write (create-only, bounded), or login-only; anything not listed is login-only, and a test fails when a route is missing from the table.
- Anonymous visitors are **create-only**: they can view data, add items, choose from *existing* sources, and scrape one item at a time. Editing or deleting existing items, sources, schedules, tags, and the admin site require login (the operator).
- Constrain what anonymous visitors can submit so they cannot steer server-side fetches: no source create/edit, `pinned_url` restricted to the linked source's own host, no `url_suffix`, no title include/exclude regexes, no free-text metadata identifiers, and a conservative character set for search terms.
- Hide connection details (`base_search_url`, `request_headers`, `request_body_template`, fetch-job URLs and raw error text) from anonymous views.
- Add **generic usage limits**, individually configurable by env var, unlimited when unset, enforced whether or not `DEMO_MODE` is on; `DEMO_MODE` only supplies defaults:
  - a hard cap on total `SearchableItem` count;
  - a per-fetch cap on rows persisted per (item, source) run;
  - a per-item fetch cooldown and a global daily fetch budget;
  - a global ceiling on stored `SearchResult` rows;
  - a lower response-size cap for demo deployments.
- Add **history retention** (a window in days, 14 in demo) as a daily background task. Because result storage is change-only, retention keeps every row inside the window plus each thread's newest row before the cutoff, and never prunes through `WebUpdate` cascades.
- Add a demo banner (remaining item and fetch quota, retention window, operator-login link) and make the nav/admin/logout controls role-aware.
- Refuse to start when `DEMO_MODE` and `DEBUG` are both on.
- Add `docs/demo_mode.md` covering the deployment shape, env vars, operator runbook, and documented future options (cookie-based visitor ownership of their own items, login throttling, closing the second admin login door).

Not changing: items never expire, cleanup is manual through the Django admin (`SearchableItem` is already registered there); no seed data; no new item-delete view; no change to relevance filtering, rate-limit pacing, or the metadata-provider design.

## Capabilities

### New Capabilities
- `demo-mode`: the `DEMO_MODE` flag, its defaults-bundle semantics, the startup guard against `DEBUG`, test-settings isolation, the demo banner, and the documentation deliverable.
- `public-access-tiers`: the route-classification table, fail-closed anonymous access, the create-only anonymous write surface, the fields and connection details hidden or locked for anonymous visitors, and the operator-only surfaces including the admin.
- `usage-limits`: the item cap, per-fetch row cap, per-item cooldown, daily fetch budget, response-size cap, and the terminal fetch-job outcomes they produce, with enforcement at a single dispatch choke point and re-checked at execution time.
- `history-retention`: the retention window, the anchor-row rule for change-only storage, cascade-safe pruning of run history, the global row ceiling and its eviction order, and the daily background task.

### Modified Capabilities
None. `retroactive-result-matching` says re-evaluating a row against current relevance criteria never deletes it; age-based retention is a separate, explicit mechanism and does not contradict that requirement (the design records the distinction). No other existing spec's requirements change.

## Impact

- **Settings**: `django_scraper/settings.py` (new env-driven flags and limits, conditional middleware class, startup guard, context processor) and `django_scraper/settings_test.py` (force `DEMO_MODE` off).
- **New modules**: a central access-policy module and its middleware; a limits module; a retention module (pure functions with a thin periodic-task wrapper, matching the `dispatch_due_schedules` convention); a context processor for the banner.
- **Models/migrations**: `FetchJob` gains new terminal statuses and a `truncated_count` field (one migration chaining off `0022_expected_value_vendor_provenance`); no change to `SearchResult`, and `SearchableItem` gains a save-time cap check with no schema change.
- **Views/forms/templates**: role-aware forms for anonymous item and item-source creation, `UpdateFromWebView` mode gating, `base.html` nav and banner, source and fetch-job partials that hide connection details.
- **Tasks**: enforcement in `dispatch_fan_out` and `fetch_one_unit`; per-fetch cap in the unit pipeline before dedupe; a new daily periodic task in the worker.
- **Dependencies**: none new.
- **Ops**: web and worker services must share the same env; recommended separate Railway environment with its own Postgres/Redis and a project usage limit (documented, not code).
- **Overlap**: the in-flight `streamline-view-terms-navigation` change (0/23 tasks done) rewrites `base.html` navigation and the `view_terms` update controls that this change makes role-aware. The design assumes that change lands first.
- **Out of scope (follow-ups)**: login throttling (prod is unthrottled today, so not demo-specific), redirecting `/admin/login/` to the app login, and cookie-based visitor ownership.
