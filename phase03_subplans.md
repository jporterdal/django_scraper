# Phase 3 Subplans — Scale & scheduling

> Agent-ready implementation plans for [plan.md](plan.md) **Phase 3 — Scale & scheduling** (§10).
> Each step below is a self-contained work unit. One agent implements one step.
>
> **Baseline (already done, Phases 1–2):** models `Source`, `SearchableItem`, `ItemSource`,
> `Tag`, `WebUpdate`, `FetchJob`, `SearchResult`; sync orchestrator `tracking/scrape.py`
> (`run_web_update` / `_run_parser_search`); `tracking/fetcher.py` (`Fetcher`, GET + retries +
> rate limit); parser registry `tracking/parsers.py` (`JSONSearchParser`, `HTMLResponseParserMixin`,
> `CCSearchParser`, `ShopifyParser`, `StorepassParser`, `sources` dict); management UI for
> Source/ItemSource/Tag; item list + item detail pages. Current migration head is
> **`0011_alter_source_key_alter_source_page_size_and_more`**. `django-environ` is configured in
> `django_scraper/settings.py` (env-driven settings, `TIME_ZONE="America/Halifax"`, `USE_TZ=True`).

---

## Agent rules (read before editing)

1. **Edit only your own step's section of this file.** When you implement "Phase 3: Step X", the
   **only** part of *this file* you may edit is that step's **Definition of done** checklist (tick
   `[x]`). Never edit another step's section, prose, or checkboxes.
2. **Environment — use exactly this in every step** (activate venv, set cwd, run the suite):
   ```bash
   source /home/ross/work/django_scraper/venv/bin/activate
   cd /home/ross/work/django_scraper
   python manage.py test tracking
   ```
   The full suite must pass (`OK`) before you consider your step done.
3. **No external services in tests.** `python manage.py test tracking` must pass with **no Redis,
   no Postgres, no network**. Any Redis/Huey work must run in **immediate (eager) mode** under
   tests; any Postgres work must keep SQLite as the default/test database; all fetch tests use
   saved fixtures or mocked `Fetcher`, never live HTTP.
4. **New dependencies:** add them to `requirements.txt` **with a pinned version** AND install them
   into the venv (`pip install <pkg>==<ver>`). Do not invent versions — install the current
   release, then pin whatever pip resolved.
5. **Migrations:** run `python manage.py makemigrations tracking` and keep the generated file.
   All new migrations chain off the current head **`0011_...`** (or a later step's migration).
   If two steps generate the same migration number, fix the `dependencies = [...]` list so they
   form a single linear chain — never two files sharing a number. State your final migration name
   in your completion notes.
6. **Result dict contract (unchanged):** each entry in `parser.results` is
   `{"title": str, "price": float, "category": str, "instock": 0|1}`. The orchestrator reads
   exactly these keys. Do not change this contract.
7. **Respect dependencies** (see overview). If a step you depend on is not yet merged, either wait
   or stub against the documented interface and say so in your completion notes.
8. **Append, don't rewrite.** In shared files (`scrape.py`, `models.py`, `views.py`, `urls.py`,
   `settings.py`, `requirements.txt`, `tests.py`) add your code; do not restructure another step's
   code. Every step appends its own `TestCase` classes to `tracking/tests.py`.

---

## Phase 3: Dependency overview

Steps map to plan.md Phase 3 bullets (§10):

| Step | Summary | plan.md bullet | Depends on | Parallel-safe with |
|------|---------|----------------|------------|--------------------|
| **1** | JSON API pagination (multi-page fetch + per-source page cap) | pagination | Baseline | 5, 6 |
| **2** | Payload-size handling for big JSON responses | payload-size | **Step 1** (shares `scrape.py`/`fetcher.py`) | 5, 6 |
| **3** | Huey + Redis background tasks + progress UI (HTMX) | Huey/Redis | Baseline (coordinate `scrape.py`/`models.py` with 1,2) | 5, 6 |
| **4** | `UpdateSchedule` model + preset cadences (Hourly/Twice Daily/Daily) | UpdateSchedule | **Step 3** (uses the Huey task + config) | 5, 6 |
| **5** | PostgreSQL migration path documented | PG path | Baseline | 1, 2, 3, 4, 6 (coordinate `settings.py`/`requirements.txt`) |
| **6** | Export CSV/JSON of price history | export | Baseline | 1, 2, 3, 4, 5 |

> A DRF read API was originally scoped as Step 7 but is **skipped for now** (see note at the end of this file and plan.md §10 Phase 4).

**Recommended order:** `1 → 2 → 3 → 4`, with `5` and `6` runnable in parallel at any time.

**Async batches:**
- **Batch A (parallel):** Step 1, Step 5, Step 6 (all only need the baseline; disjoint core logic).
- **Batch B:** Step 2 (after Step 1 — shares `scrape.py`/`fetcher.py`).
- **Batch C:** Step 3 (best after 1 & 2 land, since all three edit `scrape.py`/`models.py`; if run concurrently, serialize edits to those two files).
- **Batch D:** Step 4 (after Step 3).

**High-conflict files (coordinate / serialize):**
- `tracking/scrape.py` — Steps 1, 2, 3.
- `tracking/fetcher.py` — Steps 1, 2.
- `tracking/models.py` + `tracking/migrations/` — Steps 1, 3, 4 (chain off `0011`).
- `tracking/settings.py` (`django_scraper/settings.py`) — Steps 3, 5.
- `requirements.txt` — Steps 3, 5 (append lines; don't reorder).
- `tracking/views.py`, `tracking/urls.py`, `tracking/templates/tracking/` — Steps 3, 4, 6 (append routes/views; new templates per feature).
- `tracking/tests.py` — every step appends its own `TestCase` classes.

**Shared new interfaces introduced this phase (depend on these by name):**
- **Pagination hook (Step 1):** `JSONSearchParser.next_page_url(self, response, current_url, page_number) -> str | None` (default returns `None`); `_run_parser_search(parser, fetcher, url, headers=None, max_pages=1)`; `Source.max_pages`.
- **Background task (Step 3):** `tracking/tasks.py::run_web_update_task(webupdate_id, item_ids=None)`; `WebUpdate` progress fields (`status`, `total_searches`, `completed_searches`, `result_count`, `error_count`); progress endpoint `GET /update/<int:pk>/progress/` (name `update_progress`).

---

# Phase 3: Step 1

## Goal
Fetch **multiple pages** per (item, source) search so results aren't capped at the first page,
with a **per-source page cap** so runs stay bounded. Shopify (`prod-indexer`) paginates via a
`/page/{n}/` path segment; Storepass paginates via response-body tokens. HTML parsers (CC) stay
single-page.

**plan.md bullet:** *"JSON API pagination: multi-page fetch per source (the Shopify API
`/page/{n}` until empty; Storepass `pages`/`nextPageParameters`) with a per-source page cap; large
`page_size` to minimize requests."*

## Depends on
Baseline only. **Parallel-safe with** Steps 5, 6. Step 2 depends on this.

## Current state
- `tracking/scrape.py::_run_parser_search(parser, fetcher, url, headers=None)` does a **single**
  `fetcher.get(url, headers=headers)` then `parser.parse_response(response)`.
- `tracking/parsers.py`: `JSONSearchParser.parse_response()` calls `self._init_vars()` (resets
  `self.results`) then `parse_data(response.json())`; `add_result()` **appends**. `ShopifyParser`
  reads `data["hits"]["hits"][]`; `StorepassParser` reads `data["products"][]`.
- `tracking/models.py::Source` already has `page_size` (unused) but **no** page cap.
- Sample payloads: `tracking/fixtures/html/f2f/search_results_sample.json` (Shopify shape) and
  `tracking/fixtures/html/hfx/search_results_sample.json` (Storepass shape). Pagination field
  names are documented in `tracking/docs/f2f_investigation.md` / `hfx_investigation.md` — **read
  these** to confirm the exact Storepass token names (`pages` / `nextPageParameters` / similar)
  before coding.

## Files to touch
`tracking/models.py` (+ new migration), `tracking/parsers.py`, `tracking/scrape.py`,
`tracking/tests.py`.

## Tasks
1. **`Source.max_pages`** — add `PositiveSmallIntegerField(default=1, ...)` (verbose_name e.g.
   "Max pages to fetch per search (1 = single page)"). `default=1` preserves current behavior.
   Run `makemigrations tracking` (chains off `0011`).
2. **Pagination contract on parsers** (`tracking/parsers.py`):
   - Add to `JSONSearchParser`:
     - a page-append parse method: `def parse_next_page(self, response): self.parse_data(response.json())` (parses **without** resetting `self.results`).
     - a default hook: `def next_page_url(self, response, current_url, page_number): return None`.
   - Add the same no-op defaults to `HTMLResponseParserMixin` (`parse_next_page` → `self.feed(response.text)` with no reset; `next_page_url` → `None`) so HTML parsers remain single-page and duck-typing holds.
   - **`ShopifyParser.next_page_url`**: inspect the just-fetched `response.json()`; if
     `data["hits"]["hits"]` is empty → return `None` (stop). Otherwise increment the `/page/{n}/`
     segment in `current_url` (regex-replace `page/<n>/` → `page/<n+1>/`) and return the new URL.
   - **`StorepassParser.next_page_url`**: read the pagination token(s) confirmed from the
     investigation doc/fixture (`pages` total and/or `nextPageParameters`); if another page
     exists, build the next URL (base URL with updated page/params); else return `None`.
3. **Paginating orchestrator** (`tracking/scrape.py`):
   - Change signature to `_run_parser_search(parser, fetcher, url, headers=None, max_pages=1)`.
   - Page 1: `fetcher.get(url, headers)`; on non-200 return the existing failure `FetchOutcome`;
     else `parser.parse_response(response)` (resets + parses).
   - Loop pages `2..max_pages`: `next_url = parser.next_page_url(response, current_url, page)`;
     break if `None`; else `resp = fetcher.get(next_url, headers)`; break on non-200 (keep pages
     already gathered — log a warning); else `parser.parse_next_page(resp)`; call `fetcher.wait()`
     between page requests to honor rate limiting; update `current_url`/`response`.
   - Return `FetchOutcome(ok=True, http_status=200, result_count=len(parser.results))` with the
     accumulated results.
   - In `run_web_update`, pass `max_pages=source.max_pages` into `_run_parser_search`.
4. Keep the single-page path byte-identical when `max_pages == 1` (no behavior change for CC or
   any source that doesn't set `max_pages`).

## Testing (append `class PaginationTests` etc. to `tests.py`)
- Unit-test `ShopifyParser.next_page_url`: returns incremented `/page/2/` URL for a non-empty
  page, `None` for an empty `hits.hits`. Same style for `StorepassParser` using its token.
- Integration-test `_run_parser_search` with a **fake fetcher** whose `.get()` returns a queue of
  mocked responses (page 1 fixture, page 2 fixture, empty page) — assert results accumulate across
  pages, that it stops at the empty page, and that it stops at `max_pages`. Assert `fetcher.wait()`
  is called between pages. **No network.**
- Regression: with `max_pages=1`, exactly one `.get()` call and results equal the single-page case.

## Definition of done
- [x] `Source.max_pages` added + migration (chains off `0011`)
- [x] `parse_next_page` + `next_page_url` on `JSONSearchParser` and `HTMLResponseParserMixin`
- [x] `ShopifyParser` / `StorepassParser` `next_page_url` implemented against real payload shapes
- [x] `_run_parser_search` paginates with cap + inter-page `wait()`; `run_web_update` passes `max_pages`
- [x] `max_pages=1` behavior unchanged; new + existing tests pass (`python manage.py test tracking` → `OK`)

---

# Phase 3: Step 2

## Goal
Handle large JSON responses safely: cap response size, parse defensively, and record oversized/
malformed fetches to `FetchJob` instead of crashing a run.

**plan.md bullet:** *"Payload-size handling for big JSON responses (large Storepass responses,
~1.9 MB at `limit=30`): tune `limit`/`page_size`, stream/parse defensively, log oversized fetches
to `FetchJob`."*

## Depends on
**Step 1** (both edit `tracking/scrape.py` and `tracking/fetcher.py` — implement after Step 1 or
serialize edits to those two files). Parallel-safe with 5, 6.

## Current state
- `tracking/fetcher.py::Fetcher.get()` returns the full `requests` response with no size guard.
- `JSONSearchParser.parse_response()` calls `response.json()` with no try/except; a malformed/huge
  body would raise, caught generically in `run_web_update` as `FetchJob.Status.PARSE_ERROR`.
- `FetchJob.Status` choices: `SUCCESS, HTTP_ERROR, PARSE_ERROR, CONFIG_ERROR, EMPTY`.
- `Source.page_size` exists but is not injected anywhere.

## Files to touch
`tracking/fetcher.py`, `tracking/scrape.py`, `tracking/models.py` (+ migration for the new
`FetchJob.Status` choice), `django_scraper/settings.py` (new setting), `tracking/tests.py`.

## Tasks
1. **Response-size cap in `Fetcher`:**
   - Add ctor param `max_response_bytes` sourced in `from_settings()` from
     `settings.SCRAPE_MAX_RESPONSE_BYTES` (add setting, env-driven, default e.g. `8_000_000`; `0`
     or `None` = unlimited).
   - In `get()`: fast-path check `Content-Length` header when present; also guard the actual body
     size (either request with `stream=True` and accumulate `iter_content` up to the cap, or check
     `len(response.content)`). If the cap is exceeded, log a WARNING and raise a custom
     `ResponseTooLargeError(Exception)` (define it in `fetcher.py`) carrying the URL and size.
2. **Orchestrator handling** (`tracking/scrape.py`):
   - Add `FetchJob.Status.OVERSIZED = "oversized", "Response too large"` to the model choices
     (migration: choices change generates an `AlterField` — keep it).
   - Wrap the fetch/parse so `ResponseTooLargeError` is caught distinctly and recorded via
     `_record_fetch_job(..., FetchJob.Status.OVERSIZED, error_message=str(exc))`, counted as an
     error, and the loop continues.
   - Make JSON decoding defensive: catch `json.JSONDecodeError`/`ValueError` around
     `response.json()` (either in the parser or the orchestrator) and record `PARSE_ERROR` with a
     clear "invalid JSON" message rather than a bare traceback.
3. **Payload minimization (light):** document (docstring/comment on `Source.page_size`) that
   operators reduce payload via `Source.page_size` and a `limit`/`pageSize` param baked into
   `base_search_url`; no auto-injection required. If you add optional injection, keep it opt-in and
   non-breaking.

## Testing (append `class PayloadSizeTests` to `tests.py`)
- `Fetcher.get()` raises `ResponseTooLargeError` when a mocked response exceeds the cap (mock
  `Content-Length` and/or body); does **not** raise under the cap.
- `run_web_update` with a fetcher stubbed to raise `ResponseTooLargeError` records a `FetchJob`
  with `status="oversized"` and increments `error_count`; the run completes for other items.
- Malformed JSON body → `FetchJob` `status="parse_error"` with a clear message. **No network.**

## Definition of done
- [x] `SCRAPE_MAX_RESPONSE_BYTES` setting + `Fetcher` size cap + `ResponseTooLargeError`
- [x] `FetchJob.Status.OVERSIZED` + migration; orchestrator records oversized + defensive JSON decode
- [x] Tests cover oversized + malformed JSON paths; suite passes (`OK`)

---

# Phase 3: Step 3

## Goal
Run updates in the **background** (Huey + Redis) instead of blocking the request, and show
**live progress** via HTMX polling.

**plan.md bullet:** *"Huey + Redis background tasks + progress UI (HTMX polling)."*

## Depends on
Baseline. Coordinate `tracking/scrape.py` and `tracking/models.py` with Steps 1/2 (shared files).
Step 4 depends on this. Parallel-safe with 5, 6.

## Current state
- `tracking/views.py::UpdateFromWebView.post` calls `SearchResult.update_from_web(items=items)`
  **synchronously** and then redirects with a summary message.
- `WebUpdate` has only `timestamp` (no status/progress). `run_web_update` creates the `WebUpdate`,
  loops item-sources, bulk-creates `SearchResult`s at the end, and returns `WebUpdateStats`.
- No task queue, no HTMX. Requirements: no `huey`/`redis`.

## Files to touch
`requirements.txt`, `django_scraper/settings.py`, new `tracking/tasks.py`, `tracking/models.py`
(+ migration), `tracking/scrape.py`, `tracking/views.py`, `tracking/urls.py`,
`tracking/templates/tracking/` (progress partial + list-page wiring), `tracking/tests.py`.

## Tasks
1. **Dependencies:** add `huey` and `redis` to `requirements.txt` (pin installed versions) and
   `pip install` them into the venv.
2. **Settings:** configure Huey with `RedisHuey`, Redis URL from env
   (`env.str("REDIS_URL", default="redis://localhost:6379/0")`), and **`immediate` mode enabled by
   default for local/test** so no Redis is needed to run the suite — e.g.
   `HUEY = {"huey_class": "huey.RedisHuey", "name": "django_scraper", "immediate": env.bool("HUEY_IMMEDIATE", default=DEBUG), "connection": {"url": REDIS_URL}}`.
   Ensure tests run with `immediate=True` (DEBUG defaults True; if unsure, force immediate under
   test via settings). Add `huey.contrib.djhuey` to `INSTALLED_APPS`.
3. **`WebUpdate` progress fields** (`tracking/models.py`, + migration):
   `status` (TextChoices: `PENDING`/`RUNNING`/`DONE`/`FAILED`, default `PENDING`),
   `total_searches` (PositiveIntegerField default 0), `completed_searches` (default 0),
   `result_count` (default 0), `error_count` (default 0). (These mirror plan.md §4.1.)
4. **Refactor `run_web_update`** to report progress: accept an existing `webupdate` (create if
   None), set `status=RUNNING` and `total_searches` up front, and after each item-source increment
   `completed_searches`/`error_count` and `save(update_fields=[...])` so polling sees progress.
   On completion set `status=DONE` (or `FAILED` on unhandled error) plus final counts. Keep the
   returned `WebUpdateStats` contract intact. (Coordinate with Steps 1/2 which also edit this file.)
5. **Task** (`tracking/tasks.py`): `@task()` `run_web_update_task(webupdate_id, item_ids=None)`
   that loads the `WebUpdate`, resolves items from `item_ids`, and calls `run_web_update`. Guard
   with try/except → set `status=FAILED` on error.
6. **Views/URLs:**
   - `UpdateFromWebView.post`: create the `WebUpdate` (status `PENDING`, `total_searches` computed),
     enqueue `run_web_update_task(webupdate.pk, item_ids)`, and redirect to the list page (or render
     a page that shows the progress partial) rather than blocking.
   - Add `UpdateProgressView` → `GET /update/<int:pk>/progress/` (name `update_progress`) returning
     an **HTML partial** (progress bar + counts). Use the HTMX `HX-Trigger`/polling pattern: the
     partial polls itself (`hx-get` + `hx-trigger="every 2s"`) until `status` is `DONE`/`FAILED`,
     then stops (swap in a final summary + a "refresh" link).
7. **Templates:** include HTMX (`https://unpkg.com/htmx.org`), add the progress partial template,
   and wire the "Update selected/all" flow on `searchableitem_list.html` to show progress. Keep the
   existing message-based summary as a fallback.

## Testing (append `class BackgroundUpdateTests` to `tests.py`)
- With Huey **immediate mode** (default under test), POST to `/update/` creates a `WebUpdate`,
  runs the task inline, and ends with `status="done"` and correct counts (mock `Fetcher` — no
  network).
- `GET /update/<pk>/progress/` returns 200 and renders progress (in-progress vs done states).
- Task sets `status="failed"` when `run_web_update` raises (patch it to raise).
- Confirm the suite passes **without Redis running**.

## Definition of done
- [x] `huey` + `redis` in requirements (pinned) and installed; Huey configured with immediate mode for tests
- [x] `WebUpdate` progress fields + migration; `run_web_update` reports incremental progress
- [x] `tracking/tasks.py::run_web_update_task`; `UpdateFromWebView` enqueues instead of blocking
- [x] `update_progress` endpoint + HTMX-polling progress partial wired into the list page
- [x] Tests pass with no Redis (`python manage.py test tracking` → `OK`)

---

# Phase 3: Step 4

## Goal
Let the user define **recurring scrapes** via an `UpdateSchedule` model, executed by the
background worker. The user picks a cadence from **pre-set frequency options** — at minimum
**Hourly**, **Twice Daily**, and **Daily** — rather than a single fixed daily run.

**plan.md bullet:** *"`UpdateSchedule` model + daily scrape option."* (Extended here: the "daily"
option becomes a set of pre-set frequencies — Hourly / Twice Daily / Daily — so the model must
support sub-daily cadences, not just once per day.)

## Depends on
**Step 3** (uses `run_web_update_task` + Huey config). Parallel-safe with 5, 6.

## Current state
- `tracking/views.py`: `UpdateScheduleCreateView` is a **stub** `TemplateView`
  (`updateschedule_form.html`); `UpdateScheduleListView` currently lists `WebUpdate` rows
  (scrape history). URLs `add_update` / `view_updates` already exist.
- No `UpdateSchedule` model.

## Files to touch
`tracking/models.py` (+ migration), `tracking/tasks.py`, `tracking/forms.py`, `tracking/views.py`,
`tracking/urls.py`, `tracking/admin.py`, `tracking/templates/tracking/`, `tracking/tests.py`,
new doc `docs/scheduling.md` (repo-root `docs/`).

## Tasks
1. **`UpdateSchedule` model:**
   - `name` (Char), `enabled` (Bool default True), optional `tag` (FK `Tag`, null/blank → "all
     active items" when empty), `last_run_at` (DateTimeField null/blank).
   - **`frequency`** — a `TextChoices` field holding the pre-set cadence. Define **at least**:
     ```python
     class Frequency(models.TextChoices):
         HOURLY = "hourly", "Hourly"
         TWICE_DAILY = "twice_daily", "Twice Daily"
         DAILY = "daily", "Daily"
     ```
     Keep it a `TextChoices` so more presets (e.g. "Every 15 minutes", "Weekly") can be added
     later without a schema change beyond the choices list. Default `DAILY`.
   - **`anchor_time`** (TimeField, local America/Halifax) — the reference time-of-day used to place
     runs. Semantics per frequency (document in a model docstring/comment):
     - `DAILY` → one run per day at `anchor_time`.
     - `TWICE_DAILY` → two runs per day, at `anchor_time` and `anchor_time + 12h`.
     - `HOURLY` → one run per hour; only the **minute** of `anchor_time` matters (minute-past-the-hour).
   - `__str__` returning e.g. `"{name} ({get_frequency_display})"`. Migration (chains off Step 3's
     `WebUpdate` migration or `0011` — fix `dependencies` to keep one linear chain). Register in
     `tracking/admin.py`.
2. **Cadence / due-check logic:** a pure, unit-testable helper (e.g.
   `UpdateSchedule.is_due(self, now)` plus a module function that returns all due enabled
   schedules). Drive it off a per-frequency **interval** so sub-daily cadences work and re-runs
   can't double-fire within one period:
   - Map frequency → interval: `HOURLY → 60 min`, `TWICE_DAILY → 720 min`, `DAILY → 1440 min`
     (keep this mapping next to the `Frequency` choices so new presets add one entry).
   - A schedule is **due** when `enabled` and (`last_run_at is None` **or**
     `now - last_run_at >= interval`), with the first eligible occurrence aligned to `anchor_time`
     (for `DAILY`/`TWICE_DAILY`) or the anchor minute (for `HOURLY`). Work in local time
     (`timezone.localtime`) since `anchor_time` is Atlantic; store `last_run_at` in UTC as usual.
3. **Periodic dispatcher** (`tracking/tasks.py`): a Huey `@periodic_task(crontab(minute="*"))`
   (runs every minute; the DB holds the real cadence) that finds due schedules, creates a
   `WebUpdate`, enqueues `run_web_update_task(webupdate.pk, item_ids=<from tag or all active>)`, and
   stamps `last_run_at`. Keep the due-resolution + tag/item resolution in helpers so they're
   testable without the worker.
4. **UI — expose the presets explicitly:** replace the stub `UpdateScheduleCreateView` with a real
   `CreateView` (ModelForm), plus list/edit/delete views and templates.
   - The schedule form **must render `frequency` as a dropdown/select of the pre-set options**
     ("Hourly", "Twice Daily", "Daily") — a `forms.Select` over `Frequency.choices` (the default
     widget for a `TextChoices` field). Do **not** expose a free-form cron/interval field.
   - Render `anchor_time` as a time input, with help text explaining how it's interpreted per
     frequency (daily = that time; twice daily = that time and 12h later; hourly = that minute each
     hour). Include the `enabled` toggle and the optional `tag` selector ("All active items" when
     blank).
   - The schedule **list** view should show each schedule's `name`, human-readable frequency
     (`get_frequency_display`), `anchor_time`, tag scope, `enabled`, and `last_run_at`.
   - Routing: either repurpose `add_update`/`view_updates` for schedules and add a separate route
     for scrape history, or add `schedules/...` routes — document your choice in completion notes
     and keep existing links working.
5. **Operator doc (`docs/scheduling.md`):** write a concise operator-facing guide for scheduled
   scrapes. It must cover:
   - **Running the scheduler** — scheduled runs only fire when the Huey consumer is running
     (e.g. `python manage.py run_huey`) with Redis available; nothing schedules while the consumer
     is stopped. Note that the periodic dispatcher lives in the Huey worker (Step 3), not the web
     process, and that local **immediate mode** has no separate worker — so real periodic
     scheduling is effectively a production/long-running-process feature.
   - **Frequency preset semantics** — a plain-language table of Hourly / Twice Daily / Daily and
     how `anchor_time` is interpreted for each (daily = that time; twice daily = that time and
     +12h; hourly = that minute each hour), noting all times are America/Halifax.
   - **How due-checking works** — the dispatcher wakes every minute and uses the interval +
     `last_run_at` logic, so runs are approximate-to-the-minute (not exact-to-the-second) and won't
     double-fire within a period; a window missed while the worker was down runs once on next wake
     (no backfill).
   - **Tag scoping** — no tag = all active items; a tag = only active items carrying that tag.
   - **Operational notes** — where to see outcomes (scrape history / `WebUpdate` rows, `FetchJob`
     errors), interaction with the per-source rate-limit delay (large schedules take a while),
     disabling vs. deleting a schedule, and timezone/DST caveats around `anchor_time`.
   Keep it accurate to what you actually implement (field names, management command, routes).

## Testing (append `class UpdateScheduleTests` to `tests.py`)
- Model create + `__str__` for each `Frequency` preset.
- Due-check helper with a mocked `now` (America/Halifax), **one case per frequency**:
  - `HOURLY`: due when `last_run_at` is ≥ ~1h ago (or None) and the anchor minute has passed this
    hour; not due within the same hour after a run.
  - `TWICE_DAILY`: due at `anchor_time` and again ~12h later; not due between those windows.
  - `DAILY`: due once at/after `anchor_time`; not due again the same day.
  - Not due when `enabled=False`.
- Dispatcher (immediate Huey): a due schedule creates a `WebUpdate`, enqueues the task (mock
  `run_web_update`/`Fetcher`), and sets `last_run_at`. Tag-scoped schedule limits items. **No network.**
- Schedule CRUD views return expected status codes; the create/edit form renders a `frequency`
  select whose options include "Hourly", "Twice Daily", and "Daily".

## Definition of done
- [x] `UpdateSchedule` model with `Frequency` presets (Hourly / Twice Daily / Daily) + `anchor_time` + migration + admin registration
- [x] Interval-based due-check helper covering all presets; Huey periodic dispatcher enqueues per due schedule and stamps `last_run_at`
- [x] Real schedule CRUD UI with `frequency` rendered as a preset dropdown (no free-form cron); stub `UpdateScheduleCreateView` replaced; existing links intact
- [x] `docs/scheduling.md` operator guide written (running the consumer, preset semantics, due-checking, tag scoping, operational notes), accurate to the implementation
- [x] Tests cover every frequency preset; suite passes with no Redis (`OK`)

---

# Phase 3: Step 5

## Goal
Make PostgreSQL a supported target and **document the migration path** from local SQLite, without
breaking the SQLite-based dev/test setup.

**plan.md bullet:** *"PostgreSQL migration path documented."*

## Depends on
Baseline. Coordinate `settings.py`/`requirements.txt` with Step 3. Parallel-safe otherwise.

## Current state
- `django_scraper/settings.py` hardcodes `DATABASES` to SQLite (`BASE_DIR / db.sqlite3`).
  `django-environ` is already configured (`env = environ.Env(...)`, reads `.env_sample`).
- No Postgres driver in `requirements.txt`.

## Files to touch
`django_scraper/settings.py`, `requirements.txt`, `.env_sample`, new doc
`docs/postgres_migration.md` (repo root `docs/`), `tracking/tests.py` (small settings test optional).

## Tasks
1. **Env-driven `DATABASES`:** use django-environ's DB URL support so the default stays SQLite but
   Postgres is a drop-in via env:
   ```python
   DATABASES = {
       "default": env.db_url(
           "DATABASE_URL",
           default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
       )
   }
   ```
   Confirm `python manage.py test tracking` still uses SQLite (no `DATABASE_URL` set) and passes.
2. **Driver:** add `psycopg[binary]` (psycopg 3) to `requirements.txt` (pin installed version) and
   install it. Note in the doc that it's only required when actually using Postgres.
3. **`.env_sample`:** add a commented `# DATABASE_URL=postgres://user:pass@host:5432/dbname` example.
4. **Doc `docs/postgres_migration.md`:** step-by-step — provision PG DB/user; set `DATABASE_URL`;
   `pip install` driver; `python manage.py migrate`; data transfer options (`dumpdata` →
   `loaddata`, noting `--natural-foreign`/`--natural-primary` and that `Source.key` is a
   string PK); JSONField portability (works on both); timezone (`USE_TZ=True`, store UTC); and a
   verification checklist. Keep it accurate to this project's models.
5. Verify migrations are backend-agnostic (they are — no raw SQL). Note it in the doc.

## Testing
- Optional: a `SimpleTestCase` asserting `env.db_url` parsing returns a Postgres config when
  `DATABASE_URL` is set (use `os.environ`/`override` locally; do **not** connect). The default test
  run must remain on SQLite and pass.

## Definition of done
- [x] `DATABASES` env-driven, SQLite default preserved; tests still run on SQLite (`OK`)
- [x] `psycopg[binary]` added (pinned) + `.env_sample` example
- [x] `docs/postgres_migration.md` written and accurate to the models
- [x] Suite passes

---

# Phase 3: Step 6

## Goal
Let the user **export price history** as CSV and JSON.

**plan.md bullet:** *"Export CSV/JSON of price history."*

## Depends on
Baseline. Parallel-safe with all. Appends to `views.py`/`urls.py`/item-detail template.

## Current state
- `SearchResult` rows carry `title, price, category, instock, search_term, item, source, update`
  (with `update.timestamp`). The item detail page (`SearchableItemDetailView`,
  `searchableitem_detail.html`) already loads all results for an item.
- No export endpoints.

## Files to touch
`tracking/views.py`, `tracking/urls.py`, `tracking/templates/tracking/searchableitem_detail.html`,
`tracking/tests.py`.

## Tasks
1. **Per-item export views:**
   - `GET /item/<int:pk>/export.csv` (name `export_item_csv`) → `text/csv` streaming/`HttpResponse`
     with a header row and one row per `SearchResult` for that item: source key, search_term,
     title, price, instock, category, timestamp (localized via `timezone.localtime`).
   - `GET /item/<int:pk>/export.json` (name `export_item_json`) → `application/json` (use
     `JsonResponse`, `safe=False` for a list) with the same fields.
   - Use `Content-Disposition: attachment; filename=...` so browsers download.
2. **(Optional) all-history export:** `GET /export.csv` / `/export.json` for every item, same
   columns plus item text/id. Keep it opt-in; document if added.
3. **UI:** add "Export CSV" / "Export JSON" buttons/links to the item detail page.
4. Order rows deterministically (e.g. `-update__timestamp, source__key, price`). Use
   `select_related("source", "update")` to avoid N+1.

## Testing (append `class ExportTests` to `tests.py`)
- CSV endpoint: 200, `Content-Type: text/csv`, attachment disposition, header + expected row
  count/values for seeded results.
- JSON endpoint: 200, `application/json`, parses to a list with expected fields/values.
- Empty item: valid empty export (header-only CSV / `[]` JSON).

## Definition of done
- [x] Per-item CSV + JSON export views + URLs, with attachment headers
- [x] Export links on the item detail page
- [x] Tests cover CSV/JSON content + empty case; suite passes (`OK`)

---

> **Note:** A read-only DRF API (plan.md's *"DRF read API if external app needs prices"*) was
> considered for this phase but is **skipped for now** — it's more than is needed at present. See
> plan.md §10 Phase 4.

---

*Cross-references: pagination + payload handling (Steps 1–2) build on `tracking/scrape.py` /
`tracking/fetcher.py`; background + scheduling (Steps 3–4) build on Huey; Postgres and export
(Steps 5–6) are independent and parallelizable. Update plan.md §10 Phase 3 checkboxes as steps land.*
