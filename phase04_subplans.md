# Phase 4 Subplans — POST APIs & advanced matching

> Agent-ready implementation plans for [plan.md](plan.md) **Phase 4 — POST APIs & advanced
> matching** (§10). Each step below is a self-contained work unit; one agent implements one step.
>
> **Baseline (Phases 1–3 done):** models `Source`, `SearchableItem`, `ItemSource`, `Tag`,
> `WebUpdate` (with progress fields + `Status`), `FetchJob` (with `OVERSIZED`), `SearchResult`,
> `UpdateSchedule`. `tracking/fetcher.py` = `Fetcher` (GET + retries + rate limit + response-size
> cap via `ResponseTooLargeError`). `tracking/scrape.py` = `run_web_update(items=None,
> fetcher=None, webupdate=None)` and `_run_parser_search(parser, fetcher, url, headers=None,
> max_pages=1)` (GET-only, paginating via `parser.next_page_url`). `tracking/parsers.py` =
> `JSONSearchParser`, `HTMLResponseParserMixin`, `CCSearchParser`, `ShopifyParser`,
> `StorepassParser`, and `sources = {'cc':…, 'shopify':…, 'storepass':…}`. `tracking/matching.py`
> = `title_matches_rules()` + `result_matches_item_source()`. Huey background tasks in
> `tracking/tasks.py`. **Current migration head is `0012_source_max_pages_webupdate_completed_searches_and_more`**
> (the Phase 3 squash) — verify the latest file in `tracking/migrations/` before adding yours.

---

## Agent rules (read before editing)

1. **Edit only your own step's section of this file.** When you implement "Phase 4: Step X", the
   **only** part of *this file* you may edit is that step's **Definition of done** checklist (tick
   `[x]`). Never edit another step's section, prose, or checkboxes.
2. **Environment — use exactly this in every step** (activate venv, set cwd, run the suite):
   ```bash
   source /home/ross/work/django_scraper/venv/bin/activate
   cd /home/ross/work/django_scraper
   python manage.py test tracking
   ```
   The full suite must pass (`OK`) before you consider your step done.
3. **No external services or network in tests.** `python manage.py test tracking` must pass with no
   Redis, no Postgres, and no live HTTP. Mock fetchers; use saved fixtures under
   `tracking/fixtures/`.
4. **New dependencies:** add them to `requirements.txt` **with a pinned version** AND install them
   into the venv (`pip install <pkg>==<ver>`). Do not invent versions — install the current
   release, then pin whatever pip resolved.
5. **Migrations:** run `python manage.py makemigrations tracking`; new migrations chain off the
   current head (see baseline). If two steps generate the same number, fix `dependencies=[...]`
   into a single linear chain. See the **"Migration squashing"** section at the end — because the
   dev DB is disposable and this project is not deployed, the Phase 4 migrations MAY later be
   collapsed into one file.
6. **Result dict contract (unchanged):** each entry in `parser.results` is
   `{"title": str, "price": float, "category": str, "instock": 0|1}`. Do not change this contract
   (Step 6 is the only step that touches how `None`/out-of-stock price is *stored*, and it does so
   in `scrape.py`, not the parser contract).
7. **No specific vendor names, hosts, or URLs in committed code** (per `feedback.md`). Use the
   generic platform/parser names (`WtFiltersParser`, registry key `wtfilters`, "the POST-JSON
   vendor"). Vendor-specific request details (API URL, POST body, `Origin`/`Referer`) are entered
   at runtime on the `Source` row via the management UI — **not** hardcoded or seeded in a
   committed migration. The exempt investigation docs (`tracking/docs/wt_investigation.md`) and
   fixtures (`tracking/fixtures/html/wt/`) may be read for the concrete shapes.
8. **Append, don't rewrite.** In shared files (`models.py`, `scrape.py`, `fetcher.py`,
   `parsers.py`, `matching.py`, `forms.py`, `views.py`, `urls.py`, `settings.py`,
   `requirements.txt`) add your code; do not restructure another step's code. Put each step's new
   tests in a dedicated module `tracking/test_<topic>.py` (Django discovers `test*.py`); avoid
   editing `tracking/tests.py` unless you must update an existing assertion (document it if so).

---

## Phase 4: Dependency overview

Steps map to plan.md Phase 4 bullets (§10):

> **Note:** The former Steps 3 (Playwright fetcher), 4 (fuzzy matching), and 7 (DRF read API) are
> no longer scheduled for Phase 4. They have been moved to the **"Potential Features"** section of
> plan.md. The remaining steps keep their original numbers (1, 2, 5, 6) for stable references.

| Step | Summary | plan.md bullet | Depends on | Parallel-safe with |
|------|---------|----------------|------------|--------------------|
| **1** | POST JSON API support (`Fetcher.post`, `Source.http_method`/`request_body_template`, relax `{term}`) | POST support | Baseline | 5, 6 (coordinate `models.py`/`scrape.py`/`fetcher.py`) |
| **2** | `WtFiltersParser` (POST `data.results[]`) + registry key | WtFiltersParser | **Step 1** | 5, 6 |
| **5** | Pinned result URL per `ItemSource` (bypass search) | pinned URL | Baseline | 1, 2, 6 (coordinate `models.py`/`scrape.py`/`forms.py`) |
| **6** | `SearchResult.price` NULL for out-of-stock rows | price NULL | Baseline | 1, 2, 5 (coordinate `models.py`/`scrape.py`) |

**Recommended order:** `1 → 2`, then `5` and `6` in any order.

**Async batches:**
- **Batch A:** Step 1 (unblocks Step 2).
- **Batch B:** Step 2 (after Step 1).
- **Batch C (parallelizable if isolated):** Steps 5, 6 — logically independent, but both edit
  `models.py`/`scrape.py`, so run them in isolated git worktrees, or serialize edits to those
  shared files (and their migrations). Each adds its own migration chaining off the current head.

**High-conflict files (coordinate / serialize, or use worktrees):**
- `tracking/models.py` + `tracking/migrations/` — Steps 1, 5, 6 (chain migrations; consider squashing at the end).
- `tracking/scrape.py` — Steps 1, 5, 6.
- `tracking/fetcher.py` — Step 1.
- `tracking/parsers.py` — Step 2.
- `tracking/forms.py` — Step 5.
- `tracking/views.py`/`urls.py`/`templates/` — Steps 5, 6 (UI bits).
- `requirements.txt` / `settings.py` — Step 1 (if needed).

**Shared new interfaces introduced this phase (depend on these by name):**
- **Step 1:** `Fetcher.post(url, json=None, headers=None)` (and/or `Fetcher.request(method, url, ...)`); `Source.http_method` (`GET`/`POST`), `Source.request_body_template` (JSON), `Source.build_request_body(term) -> dict | None`; relaxed `Source.build_search_url` (no `{term}` requirement when `http_method == "POST"`); `_run_parser_search(..., method="GET", body=None)`.

---

# Phase 4: Step 1

## Goal
Add POST JSON API support to the fetch path so vendors whose search API is POST-only (search term
in a JSON request body, not the URL) can be scraped.

**plan.md bullet:** *"POST JSON API support: `Fetcher` generic `request()`/`post()` (add POST to
`Retry.allowed_methods`); `Source.http_method` + `request_body_template` (`{term}` injected into
JSON body); relax `build_search_url` `{term}` requirement when method is POST."*

## Depends on
Baseline. **Unblocks Step 2.** Shares `models.py`/`scrape.py`/`fetcher.py` with Steps 5/6 —
coordinate or isolate.

## Current state
- `tracking/fetcher.py::Fetcher` has only `get(url, headers=None)`; `Retry(allowed_methods=["GET"])`;
  `_enforce_size_cap` runs inside `get`.
- `tracking/models.py::Source.build_search_url(term, url_suffix="")` **raises `ValueError`** if
  `"{term}"` isn't in `base_search_url`. No `http_method`/`request_body_template`.
- `tracking/scrape.py::_run_parser_search(parser, fetcher, url, headers=None, max_pages=1)` does
  `fetcher.get(...)` for page 1; `run_web_update` builds the GET URL and passes headers.

## Files to touch
`tracking/fetcher.py`, `tracking/models.py` (+ migration), `tracking/scrape.py`, and a new test
module `tracking/test_post_support.py`.

## Tasks
1. **`Fetcher` POST** (`tracking/fetcher.py`):
   - Add `"POST"` to `Retry(allowed_methods=[...])`.
   - Add `post(self, url, json=None, headers=None)` (and optionally a generic
     `request(self, method, url, json=None, headers=None)` that `get`/`post` delegate to) that
     issues the request via `self._session`, applies the same logging + `self._enforce_size_cap`,
     and returns the response. Keep `get` behavior identical.
2. **`Source` fields** (`tracking/models.py`, + migration):
   - `http_method` — `CharField(choices=[("GET","GET"),("POST","POST")], default="GET", max_length=4)`.
   - `request_body_template` — `JSONField(default=dict, blank=True)` holding the POST body with the
     `"{term}"` placeholder in string leaves (e.g. `{"q": "{term}", "context": {...}}`).
   - `build_request_body(self, term) -> dict | None`: return `None` when `http_method != "POST"` or
     the template is empty; otherwise deep-copy the template and replace the substring `"{term}"`
     in every string leaf with the raw term (NOT URL-encoded — POST-body APIs want the plain term).
   - **Relax `build_search_url`:** when `http_method == "POST"`, do not require `"{term}"`; return
     `base_search_url` (still applying `url_suffix`). Keep GET behavior (require `{term}`) unchanged.
3. **Orchestrator** (`tracking/scrape.py`):
   - Extend `_run_parser_search(parser, fetcher, url, headers=None, max_pages=1, method="GET", body=None)`:
     page 1 issues `fetcher.post(url, json=body, headers=headers)` when `method == "POST"`, else
     `fetcher.get(...)`. Leave the existing GET pagination loop as-is (POST pagination is out of
     scope here — see note).
   - In `run_web_update`, compute `method = source.http_method` and
     `body = source.build_request_body(search_term)`, and pass them through. GET sources are
     unaffected (`method="GET"`, `body=None`).
   - **Note (POST pagination):** POST APIs page via a body field (e.g. `context.page`), which the
     URL-based `next_page_url` contract doesn't express. Keep POST single-page for now
     (`max_pages` effectively 1 for POST); document this. A future enhancement can add a
     body-based paginator.

## Testing (`tracking/test_post_support.py`)
- `Fetcher.post` calls the session with `json=` and applies the size cap (mock session/response).
- `Source.build_request_body` substitutes `{term}` in nested string leaves; returns `None` for GET.
- `Source.build_search_url` no longer raises for a POST source lacking `{term}`; still raises for GET.
- `run_web_update` with a mocked fetcher: a POST source triggers `fetcher.post(url, json=<body>, headers=...)`
  with the term injected; a GET source still uses `fetcher.get`. **No network.**

## Definition of done
- [x] `Fetcher.post` (+ optional `request`) with POST in `Retry.allowed_methods` and size cap applied
- [x] `Source.http_method` + `request_body_template` + `build_request_body`; `build_search_url` relaxed for POST (+ migration)
- [x] `_run_parser_search`/`run_web_update` issue POST with injected body when configured; GET unchanged
- [x] Tests cover POST fetch + body injection + relaxed URL; suite passes (`OK`)

---

# Phase 4: Step 2

## Goal
Add a parser for the POST-JSON search app (registry key `wtfilters`) that maps its
`data.results[]` payload to the result contract.

**plan.md bullet:** *"`WtFiltersParser`: POST `/api/search` with `context` body + `Origin`/`Referer`;
parse `data.results[]`; register `wtfilters` + its `Source` row."*

## Depends on
**Step 1** (POST fetch + `Source.http_method`/`request_body_template`).

## Current state
- No `wtfilters` parser. `sources` has `cc`/`shopify`/`storepass`.
- Reference (exempt): `tracking/docs/wt_investigation.md` documents the API and payload; fixture
  `tracking/fixtures/html/wt/search_results_sample.json` holds a sample response.

## Files to touch
`tracking/parsers.py`, and a new test module `tracking/test_wtfilters_parser.py`.

## Tasks
1. **`WtFiltersParser(JSONSearchParser)`** in `tracking/parsers.py`: implement `parse_data(data)`
   over `data.get("data", {}).get("results", [])`, emitting one `add_result` per result:
   - `title` = the result's title field; `price` = float price; `instock` = truthiness of the
     in-stock boolean; `category` = subcategory or category. Confirm exact JSON keys against
     `tracking/fixtures/html/wt/search_results_sample.json` (and `wt_investigation.md`).
   - Search results collapse variants (one row per product). Do **not** implement secondary
     per-variant fetches — product-level price is the scope here (note it in the docstring).
   - Keep the class docstring generic (no vendor name/host).
2. **Register** `'wtfilters': WtFiltersParser` in the `sources` dict.
3. **No committed `Source` row / no data migration.** The POST-JSON `Source` (API URL,
   `http_method="POST"`, `request_body_template` with the `context` body + `{term}`,
   `request_headers` with `Origin`/`Referer`) is created at runtime via the Source-management UI
   (per rule 7). Document the expected Source configuration in the parser docstring or a short
   comment referencing `tracking/docs/wt_investigation.md`.

## Testing (`tracking/test_wtfilters_parser.py`)
- Load the WT fixture, run `WtFiltersParser().parse_response(mock_response)` (mock `.json()` to the
  fixture dict), and assert ≥1 result with correct `title`/`price`/`instock`/`category` and the
  result-dict contract shape. **No network.**

## Definition of done
- [x] `WtFiltersParser` parses `data.results[]` to the result contract (keys verified vs fixture)
- [x] Registered as `wtfilters`; no committed vendor Source row / no vendor names in code
- [x] Fixture-based parser test passes; suite passes (`OK`)

---

# Phase 4: Step 5

## Goal
Allow a `ItemSource` to pin a specific result URL so the scraper fetches it directly, bypassing the
search step for stubborn listings.

**plan.md bullet:** *"Pinned result URL per `ItemSource` (bypass search for stubborn listings)."*

## Depends on
Baseline. Shares `models.py`/`scrape.py`/`forms.py` with Steps 1/6 — coordinate or isolate.

## Current state
- `run_web_update` always builds a search URL from `source.build_search_url(term, url_suffix)` and
  parses search results. No way to target a specific product URL.
- `ItemSource` has no pinned URL.

## Files to touch
`tracking/models.py` (+ migration), `tracking/scrape.py`, `tracking/forms.py`, and a new test
module `tracking/test_pinned_url.py`. (Optional UI note on the ItemSource form/template.)

## Tasks
1. **`ItemSource.pinned_url`** (+ migration): `URLField(max_length=1000, blank=True, default="",
   verbose_name="Pinned result URL — fetch this directly instead of running a search")`.
2. **Orchestrator** (`tracking/scrape.py`): when `item_source.pinned_url` is set, use it as the
   fetch target instead of `source.build_search_url(...)` (skip the `{term}`/build step and any
   POST body — a pinned URL is fetched with GET). Everything else (parser selection, `FetchJob`
   recording, result storage) stays the same. The source's parser must be able to parse the
   pinned endpoint's response.
   - **Design note / open decision:** search parsers expect a *search results* payload. Document
     that a pinned URL should point at an endpoint the configured parser can handle (e.g. a
     single-item search/JSON endpoint). If the parser can't parse a product page, that's a parser
     concern outside this step. Keep this step's scope to: field + "fetch pinned URL instead of
     building the search URL", with graceful `FetchJob` error handling if parsing yields nothing.
3. **Form/UI:** expose `pinned_url` on `ItemSourceForm` with help text explaining it bypasses search.

## Testing (`tracking/test_pinned_url.py`)
- With `pinned_url` set, `run_web_update` fetches the pinned URL (assert the mocked fetcher is
  called with that URL, and `build_search_url` is NOT used for that item-source).
- With `pinned_url` empty, behavior unchanged (search URL used). **No network.**

## Definition of done
- [x] `ItemSource.pinned_url` + migration; form field exposed
- [x] `run_web_update` fetches the pinned URL directly when set; normal search path unchanged otherwise
- [x] Tests cover pinned vs unpinned; suite passes (`OK`)

---

# Phase 4: Step 6

## Goal
Store `NULL` price for out-of-stock results instead of a numeric fallback, matching the decided
price semantics (§4.2).

**plan.md bullet:** *"`SearchResult.price` NULL migration for out-of-stock rows (JSON `instock`
already derived from inventory quantity)."*

## Depends on
Baseline. Shares `models.py`/`scrape.py` with Steps 1/5 — coordinate or isolate.

## Current state
- `SearchResult.price = FloatField(null=False, blank=False)`. `scrape.py` stores
  `result["price"]` (a float; parsers coerce via `add_result`, so OOS variants currently store the
  API's price or `0.0`).
- Summaries already filter `instock=1` (`SearchableListView`, `SearchableItemDetailView`), so
  charts/"cheapest" only consider in-stock rows.

## Files to touch
`tracking/models.py` (+ migration), `tracking/scrape.py`, and wherever prices are rendered/exported
if they assume non-null (`tracking/views.py`, item-detail/list templates, `test_export.py`'s
targets). New test module `tracking/test_price_null.py`.

## Tasks
1. **Model** (+ migration): change `SearchResult.price` to `FloatField(null=True, blank=True)`.
   (Dev DB is disposable — no data backfill needed; the migration is a schema `AlterField`.)
2. **Ingest** (`tracking/scrape.py`): when building the `kws` dicts, store `price=None` for rows
   whose `instock` is falsy (out of stock), keeping the numeric price for in-stock rows. (The
   parser result contract still yields a float; the *storage* decision lives in `scrape.py`.)
3. **Rendering/robustness:** ensure `None` prices don't break display/export — item list "latest
   price" and detail charts already filter `instock=1` (fine); guard any template/CSV/JSON path
   that formats `price` so `None` renders as blank/`null` rather than erroring. Coordinate with
   Step-independent export code (don't rewrite it — just handle `None`).
4. Confirm aggregates: `Min("price")` with `instock=1` filter is unaffected; add a guard if any
   query includes out-of-stock rows.

## Testing (`tracking/test_price_null.py`)
- After a run (mocked fetcher/results with a mix of in-stock and out-of-stock variants),
  out-of-stock `SearchResult`s have `price IS NULL` and in-stock ones keep their price.
- List/detail/export paths render with `None` prices without error. **No network.**

## Definition of done
- [x] `SearchResult.price` nullable + migration
- [x] `scrape.py` stores `None` for out-of-stock rows; in-stock prices preserved
- [x] Display/export paths handle `None`; suite passes (`OK`)

---

## Migration squashing (whole-phase cleanup)

Steps 1, 5, 6 each add a schema migration. Because **the dev/local DB is disposable and this
project is not deployed**, the Phase 4 migrations may be collapsed into a single migration once the
steps you want are implemented:

- **Precondition:** only squash migrations that are **not yet committed to git** (avoid rewriting
  shared history). Check `git status` / `git ls-files tracking/migrations/` first — collapse only
  the untracked Phase 4 files.
- **Method (schema-only — all Phase 4 steps are AddField/AlterField, no `RunPython`):** delete the
  untracked Phase 4 migration files, then regenerate a single one:
  ```bash
  source /home/ross/work/django_scraper/venv/bin/activate
  cd /home/ross/work/django_scraper
  rm tracking/migrations/00NN_*.py   # only the untracked Phase 4 files
  python manage.py makemigrations tracking   # one consolidated migration off the current head
  rm -f db.sqlite3 && python manage.py migrate
  python manage.py makemigrations --check --dry-run   # -> No changes detected
  python manage.py test tracking                       # -> OK
  ```
  (Alternatively `python manage.py squashmigrations tracking <first> <last>`; delete+regenerate is
  tidier for schema-only changes.)
- Do this as a final cleanup pass (not inside an individual step), so each step remains
  independently implementable with its own migration during development.

---

*Cross-references: POST support (Step 1) unblocks the POST-JSON parser (Step 2); pinned URLs
(Step 5) and NULL price (Step 6) are independent feature slices sharing `models.py`/`scrape.py`.
(The former Steps 3/4/7 — Playwright fetcher, fuzzy matching, DRF read API — have been moved to
the "Potential Features" section of plan.md and are no longer scheduled for Phase 4.) Update
plan.md §10 Phase 4 checkboxes as steps land.*
