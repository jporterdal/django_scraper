# django_scraper — Design Plan

> Living document derived from [brain.spec](brain.spec). Last updated with locked-in decisions (Jul 2026).

---

## 1. Vision & Scope

**Goal:** Track item pricing over time by gently scraping vendor search-result pages, storing timestamped snapshots locally, and surfacing trends in a Django web UI.

**In scope (this app):**
- Item identity via search terms (not canonical product IDs)
- Multiple user-defined price sources per item
- HTML search-result parsing (title, price, stock status)
- On-demand and selective price refresh (manual trigger; scheduled later)
- Historical price storage and visualization
- App-local metadata: priority tiers, tags, active/inactive flag
- **Generic item model** — equally suited to GPUs, MTG cards, and other product types

**Out of scope (for now):**
- Rich item metadata (manufacturer, oracle text, specs) — delegated to a future external app
- Official vendor APIs (sources are search URLs / HTML pages)
- Multi-user auth / sharing — **single-user personal/local tool**
- Multi-currency — **CAD only**
- Mobile UI — **desktop-first**

---

## 2. Current State (baseline)

| Area | Status |
|------|--------|
| Django project + `tracking` app | ✅ Bootstrapped |
| Core models (`Source`, `SearchableItem`, `ItemSource`, `WebUpdate`, `SearchResult`) | ✅ Initial migration |
| `search_scrape` submodule (`SearchParser` + `CCSearchParser`) | ✅ Working prototype |
| Bulk web update (`SearchResult.update_from_web`) | ✅ Basic; no rate limit, bare `except` |
| List view + Chart.js sparklines + DataTables | ✅ Prototype UI |
| Tags, per-item source config, update scheduling | ❌ Not implemented |
| Rate limiting, logging, error reporting | ❌ Not implemented |
| Tests | ❌ Empty stub |

**Notable gaps vs locked-in requirements:**
- No tag-based grouping or selective update (updates all active `ItemSource` rows)
- `Source` model has no URL template — URL is hardcoded in `CCSearchParser`
- `CCSearchParser` has GPU-vendor-specific `title_patterns` — needs generic `ItemSource`-level rules
- Timestamps display in UTC; should show **America/Halifax**
- `UpdateSchedule*` views are stubs (deferred — manual-only for now)
- Out-of-stock uses `price = 0` fallback; target is **NULL price + stock flag** (low priority)
- Item variant disambiguation is regex-only at parser level; user should judge from stored results

---

## 3. Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Django Web UI  (Bootstrap + DataTables + Chart.js)         │
│  - Item CRUD, tag filter, source assignment                 │
│  - "Update selected" / "Update all" buttons                 │
│  - Price history charts; full result list per item          │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (sync; background later)
┌──────────────────────────▼──────────────────────────────────┐
│  Fetch Orchestrator                                         │
│  - Build work queue from selection (items / tags / all)     │
│  - Sequential fetch + per-source delay + jitter             │
│  - Retry / failure recording (FetchJob)                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  Parser Registry  (parser_key → Parser class)               │
│  submodule: SearchParser (HTML) → cc: CCSearchParser        │
│  tracking:  JSONSearchParser →                              │
│    shopify: ShopifyParser | storepass: StorepassParser (JSON)│
└──────────────────────────┬──────────────────────────────────┘
                           │ requests GET/POST → JSON API (Playwright: potential/unscheduled)
┌──────────────────────────▼──────────────────────────────────┐
│  Vendor JSON search APIs (Shopify prod-indexer, Storepass,   │
│  POST JSON apps) — product/pricing data as JSON, not HTML DOM│
└─────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  SQLite (local) / PostgreSQL (remote)                       │
│  WebUpdate → SearchResult (all parsed rows + search term)   │
└─────────────────────────────────────────────────────────────┘
```

**Design principles:**
- Keep `search_scrape` as a **reusable parsing library** in a **separate repo** (git submodule).
- Django-specific orchestration, models, and UI stay in `tracking`.
- Parsers and matching rules must be **vendor/item-type agnostic** — no GPU-only assumptions in core models.

---

## 4. Data Model — Locked Design

### 4.1 Model changes

| Model | Changes |
|-------|---------|
| `SearchableItem` | Add `tags` (M2M). Optional `notes` for disambiguation hints visible in UI. |
| `Source` | Add `base_search_url` template (`{term}` placeholder for GET APIs). Add `parser_key` (registry selector, **decoupled** from PK `key` so several vendors can share `shopify`/`storepass`). Add `http_method` (`GET`/`POST`), `request_headers` (JSON — e.g. `Accept: application/json`, `Origin`, `Referer`), `request_body_template` (JSON string with `{term}` for POST APIs). `currency` default `CAD`. Optional `request_delay_seconds`, `page_size`. Widen `key`/`base_search_url` (long API URLs; store IDs, product-line params). |
| `ItemSource` | Add optional `url_suffix`, `title_include_patterns` (JSON/list), `title_exclude_patterns`. Per-item+source disambiguation without parser hardcoding. |
| `WebUpdate` | Add `status`, `item_count`, `error_count` (no `triggered_by` — single user). |
| `SearchResult` | Add `search_term` (the term actually queried). Store **all parsed result rows** from each fetch. `price` nullable (`NULL` when out of stock — migrate when convenient). |
| `Tag` | **New.** Name, optional color; M2M on `SearchableItem`. |
| `FetchJob` | **New.** Per (item, source, webupdate): HTTP status, error message, duration_ms, success/fail. |

### 4.2 Price & result semantics (decided)

| Question | Decision |
|----------|----------|
| Which results to store? | **All parsed matches** from the search page for that term+source. User reviews relevance in UI; charts can default to lowest in-stock. |
| Out of stock | **`instock` flag + `price = NULL`** (preferred; low-priority migration from current `0.0`). |
| Currency | **CAD only** — no conversion logic needed. |
| "Best price" for summaries | Computed at query/display time (`Min` where `instock=True` and `price IS NOT NULL`), not by discarding rows at ingest. |

### 4.3 Generic matching strategy

Because MTG and GPUs share the same models, matching rules live on **`ItemSource`**, not in parser subclasses:

1. **Include patterns** — regex/keywords; result title must match at least one (if any configured).
2. **Exclude patterns** — result dropped if title matches (e.g. "used", wrong printing).
3. **Parser defaults** — only `term$` end-anchor; vendor-specific patterns removed from `CCSearchParser` over time.
4. **Phase 4:** pinned result URL for stubborn listings. **Potential (unscheduled):** fuzzy match.

**JSON APIs emit one row per variant/condition.** Shopify and Storepass return multiple condition variants (NM/PL/HP, Foil, set printings) per product. The parser stores **one `SearchResult` per variant**, so include/exclude patterns are now the primary tool for narrowing "variant explosion" to the printing/condition the user actually tracks. This raises the importance of Phase 2 pattern fields relative to the old HTML plan.

---

## 5. Scraping & Parsing Strategy

### 5.1 Parser plugin pattern

Parsers are keyed by **platform** (`Source.parser_key`), not by individual vendor, so several stores that share a backend reuse one parser:

```python
sources = {
    'cc': CCSearchParser,          # Phase 1 — Canada Computers (HTML, from search_scrape submodule)
    'shopify': ShopifyParser,      # Phase 2 — Shopify prod-indexer JSON  [in tracking]
    'storepass': StorepassParser,  # Phase 2 — Storepass SaaS JSON         [in tracking]
    'wtfilters': WtFiltersParser,  # Phase 4 — POST JSON search app        [in tracking]
}
```

- **Where parsers live:** HTML parsers (`SearchParser`, `CCSearchParser`) stay in the `search_scrape` **submodule** (HTML-only). All **JSON** parsers and their base class live in **`tracking`** (see 5.2) — JSON parsing shares almost no logic with the HTML DOM engine, so it doesn't belong in the HTML library.
- The orchestrator builds the request from the `Source` (method, URL from `base_search_url` + `ItemSource.url_suffix`, headers, POST body) and hands the **response** to the parser; the parser owns response→`results` mapping.
- Registration stays in `tracking/parsers.py` (developer-maintained for now; user is developer).
- Parsers are **duck-typed**, not required to share a base class: the orchestrator only needs `parse_response(response)` and `.results` (list of `{title, price, category, instock}`). Title matching moves to `ItemSource` / `tracking/matching.py` (sec 4.3).

### 5.2 Parsing approach — JSON APIs (chosen)

Vendor investigations all found the same shape: **products are rendered client-side (Alpine/React), but a JSON search API returns structured products fetchable with `requests`** — no HTML DOM scraping and no browser needed. The Phase 2 approach is therefore **JSON API parsing**, replacing the earlier HTML/BeautifulSoup plan.

| Parser | Backend | Fetch | Result mapping | Parser home |
|--------|---------|-------|----------------|-------------|
| **CC (existing)** | Server-rendered HTML | GET | `SearchParser` (`HTMLParser`) DOM traversal | `search_scrape` submodule |
| `ShopifyParser` | Shopify `prod-indexer` (Elasticsearch-style) | GET, `Accept: application/json` | `hits.hits[]._source` → one row per `variants[]` | `tracking` |
| `StorepassParser` | Storepass SaaS | GET, `Accept`/`Origin`/`Referer` | `products[]` → one row per `variantInfo[]` | `tracking` |
| `WtFiltersParser` (Phase 4) | POST JSON search app | **POST** JSON body (`q` in body) | `data.results[]` | `tracking` |
| **No JSON API** | — | Playwright (last resort, **potential/unscheduled**) | Only if no fetchable JSON endpoint exists | `tracking` |

A small base class **`JSONSearchParser`** lives in **`tracking`** (e.g. `tracking/parsers.py`), **not** in the `search_scrape` submodule — JSON parsing (`response.json()` + dict/list navigation) shares almost nothing with the submodule's HTML DOM engine (`Element`, DOM stack, `check_element_*`/`read_*`). It parses `response.json()` into `results` with the same dict keys. Prices come back as floats (no `$`/currency string parsing); `instock` is derived from inventory quantity (`> 0`). Both HTML and JSON parsers expose a uniform `parse_response(response)` so `scrape._run_parser_search` treats them identically (the HTML `SearchParser` gets a thin `parse_response` wrapper — implemented in `tracking`, e.g. a mixin/adapter — that calls `feed(response.text)`, leaving the submodule untouched).

*Optional (stretch):* a config-driven `JSONSearchParser` that reads JSON paths (container / title / price / instock) from `Source`, so new Shopify-family stores need a DB row rather than a new class. Default to per-platform classes; adopt config-driven only if a third Shopify variant appears.

### 5.3 HTTP client

**Chosen:** `requests` wrapped in a shared `Fetcher` class — `Session`, retries on 429/503, per-source delay from `Source.request_delay_seconds`. Extended for JSON APIs:

- **Per-source headers** merged from `Source.request_headers` (e.g. `Accept: application/json`, `Origin`, `Referer`) on top of the default User-Agent.
- **GET + POST**: a generic `request(method, url, headers, json)` (Phase 2 uses GET; Phase 4 adds POST for the POST-JSON API). POST added to `Retry(allowed_methods=...)`.

### 5.4 Rate limiting

**Chosen:** Sequential fetch with **2–5s default delay + jitter** between requests. Acceptable for **tens–200 items** (user confirmed longer runs OK). No background queue until scheduled updates are added.

---

## 6. Update Orchestration

### 6.1 Sync-first (decided)

| Phase | Approach |
|-------|----------|
| **Now** | Synchronous view + delay loop — fine for ≤200 items |
| **Later** (scheduled daily scrape) | **Huey + Redis** — Redis is acceptable for remote deploy |

No Celery unless complexity demands it. Django-Q2 remains a fallback.

### 6.2 Selection API

```
POST /update/
  item_ids: [1, 2, 3]   # optional
  tag_ids: [4]          # optional
  source_keys: ["cc"]   # optional; default all linked sources
```

If neither `item_ids` nor `tag_ids` → all `active=True` items.

### 6.3 Timestamps

- **Store:** UTC in DB (`USE_TZ=True`).
- **Display:** **`TIME_ZONE = "America/Halifax"`** (hard-coded Atlantic).

---

## 7. UI / Frontend

### 7.1 Stack (decided)

**Primary:** Bootstrap 5 + DataTables + Chart.js — ship features first.

**Optional later:** HTMX for update-progress polling when background jobs arrive; ECharts on item detail if multi-source charts need richer tooltips.

**Not now:** Mobile layout, SPA framework.

### 7.2 Near-term pages

| Page | Purpose |
|------|---------|
| Item list (existing) | Fix links; tag filter; checkboxes; "Update selected" / "Update all" |
| Item detail / history | Full price chart (lowest in-stock default); **table of all stored results** so user can judge relevance |
| Source management | CRUD for `Source` + parser key + URL template |
| Item ↔ Source assignment | `ItemSource` + include/exclude patterns |
| Tag management | CRUD + filter |

---

## 8. Deployment & Environments

| Concern | Local | Remote |
|---------|-------|--------|
| Database | SQLite | PostgreSQL |
| Config | `.env` via django-environ | Same |
| Timezone | `America/Halifax` | Same |
| Static / process | `runserver` | Gunicorn + reverse proxy |
| Background jobs | N/A (sync) | Huey + **Redis** when scheduling added |

**Action item:** Add `django-environ` to root `requirements.txt`.

---

## 9. Observability & Quality

| Concern | Choice |
|---------|--------|
| Logging | stdlib `logging` (structlog optional later) |
| Fetch failures | `FetchJob` model + admin readonly view |
| Tests | **pytest-django** + saved fixtures per source (HTML for CC; **JSON** for JSON-API vendors) |
| Fixtures | `tracking/fixtures/html/cc/`; JSON: `tracking/fixtures/html/f2f/*.json`, `.../hfx/*.json` |

---

## 10. Phased Roadmap (updated)

### Phase 1 — Solidify core loop (MVP)
- [x] Fix UI links; "Update all active" + selective update (checkboxes / tag filter)
- [x] Rate limiting + proper logging in fetch orchestrator (replace bare `except`)
- [x] `Source.base_search_url` + migrate CC URL out of parser
- [x] `Tag` model + list filter
- [x] `SearchResult.search_term`; persist all parsed rows per fetch
- [x] `FetchJob` error capture
- [x] `TIME_ZONE = "America/Halifax"`; format timestamps in templates
- [x] Add `django-environ` to requirements
- [x] Remove GPU-specific patterns from `CCSearchParser`; stub `ItemSource` pattern fields
- [x] Parser HTML fixture test for `CCSearchParser`

### Phase 2 — JSON API vendors (Shopify + Storepass) + disambiguation

> **Approach change (Jul 2026):** Investigations of the tracked vendors all found products are
> rendered client-side but exposed via **JSON search APIs** fetchable with `requests`. Phase 2
> now parses **JSON APIs**, not HTML/DOM. Initial scope: **GET**-based APIs — **Shopify**
> (`prod-indexer`) and **Storepass**. POST-only APIs move to Phase 4.
> See `tracking/docs/{f2f,hfx,wt}_investigation.md`.

**2a — JSON parsing infrastructure (do first; unblocks all JSON vendors)**
- [x] Investigate vendor search pages → all `JS_RENDERED_WITH_JSON_API` (see `tracking/docs/{f2f,hfx,wt}_investigation.md`)
- [ ] Add `JSONSearchParser` base class **in `tracking`** (not the submodule) that maps `response.json()` → `results` (`{title, price, category, instock}`). Leave `search_scrape` HTML-only.
- [ ] Uniform `parse_response(response)` contract; refactor `scrape._run_parser_search` to call it instead of hardcoded `feed(html)`. Give the submodule's HTML `SearchParser` a `parse_response` wrapper via a **`tracking`-side mixin/adapter** (`feed(response.text)`) so the submodule stays untouched
- [ ] `Fetcher`: merge per-source `request_headers` (`Accept: application/json`, `Origin`, `Referer`) over the default User-Agent (GET only this phase)
- [ ] `Source` model: add `parser_key` (registry selector, decoupled from PK), `request_headers` (JSON), optional `page_size`; widen `key`/`base_search_url`; migration

**2b — Platform parsers + vendor Source rows (GET APIs)**
- [ ] `ShopifyParser` (`prod-indexer`): `hits.hits[]._source` → one row per `variants[]` (float price, `instock = inventoryQuantity > 0`, category = set name); register `shopify`
- [ ] `StorepassParser`: `products[]` → one row per `variantInfo[]` (display_name + condition, float price, `instock = inventory_quantity > 0`); register `storepass`; `store_id`/`product_line` carried in `base_search_url` (product_line overridable via `ItemSource.url_suffix`)
- [ ] `Source` rows for the Shopify/Storepass vendors are created at runtime via the Source-management UI (not committed data migrations)
- [ ] JSON fixtures + parser tests: `tracking/fixtures/.../f2f/search_results_sample.json`, `.../hfx/...json`; assert ≥1 variant row with correct title/price/instock; **no HTTP in tests**

**2c — Disambiguation & UI (retained; adjusted for per-variant rows)**
- [ ] `ItemSource` include/exclude pattern fields in user forms + apply matching (now central: filters NM/PL/Foil/set variant explosion)
- [ ] Item detail page: all-results table + per-source Chart.js history (variant rows visible; pattern-matched rows highlighted)
- [ ] Source + ItemSource management UI (`Source` form exposes `parser_key` dropdown from registry, `request_headers`, URL template; validate `{term}` for GET parsers)

> **Subplans:** Agent-ready implementation steps for this phase live in
> [phase02_subplans.md](phase02_subplans.md) (JSON-API aligned). The earlier HTML/DOM
> version is archived in [phase02_subplans_old.md](phase02_subplans_old.md) (superseded).

### Phase 3 — Scale & scheduling
- [ ] **JSON API pagination**: multi-page fetch per source (the Shopify API `/page/{n}` until empty; Storepass `pages`/`nextPageParameters`) with a per-source page cap; large `page_size` to minimize requests
- [ ] **Payload-size handling** for big JSON responses (large Storepass responses, ~1.9 MB at `limit=30`): tune `limit`/`page_size`, stream/parse defensively, log oversized fetches to `FetchJob`
- [ ] Huey + Redis background tasks + progress UI (HTMX polling)
- [ ] `UpdateSchedule` model + daily scrape option
- [ ] PostgreSQL migration path documented
- [ ] Export CSV/JSON of price history
- [ ] ~~DRF read API if external app needs prices~~ — **skipped for now** (more than currently needed; moved to **Potential Features** below). Not covered in [phase03_subplans.md](phase03_subplans.md).

### Phase 4 — POST APIs & advanced matching
- [x] **POST JSON API support**: `Fetcher` generic `request()`/`post()` (add POST to `Retry.allowed_methods`); `Source.http_method` + `request_body_template` (`{term}` injected into JSON body); relax `build_search_url` `{term}` requirement when method is POST
- [x] `WtFiltersParser`: POST `/api/search` with `context` body + `Origin`/`Referer`; parse `data.results[]`; register `wtfilters` + its `Source` row
- [x] Pinned result URL per `ItemSource` (bypass search for stubborn listings)
- [x] `SearchResult.price` NULL migration for out-of-stock rows (JSON `instock` already derived from inventory quantity)

> **Subplans:** Agent-ready implementation steps for this phase live in
> [phase04_subplans.md](phase04_subplans.md).

#### Suggestions Within Phase 4 (proposed; not yet in subplans)
Enhancements to the Phase 4 steps above, captured here for later inspection. **Not** yet folded
into [phase04_subplans.md](phase04_subplans.md).
- [ ] **Blocked/rate-limited detection** (brain.spec's CAPTCHA/anti-bot concern): add a `BLOCKED` `FetchJob.Status` sibling to `OVERSIZED` — when a JSON parser receives HTML (a challenge page) or a 403/429, record it distinctly instead of a generic parse error. Pairs with Steps 1–2 and makes the POST vendor (which needs correct `Origin`/`Referer`) far easier to debug.
- [ ] **POST pagination hook**: Step 1 keeps POST single-page (its URL-based `next_page_url` contract can't express body-paged APIs like `context.page`). Promote the deferred "body-based paginator" note to an explicit optional Step 4 item so a multi-page POST vendor doesn't silently truncate results.
- [ ] **Pinned-URL parser path** (Step 5): document that a pinned URL should point at the vendor's single-item **JSON/search endpoint** (so the existing parser works); if parsing yields zero rows, record a `FetchJob` error rather than silently storing nothing.

### Potential Features (unscheduled)
Considered but **not** currently scheduled for a phase. Promote into a phase if/when needed.
- [ ] Playwright fetcher — **last resort only** for vendors with **no** usable JSON API (per-source flag on `Source`)
- [ ] Fuzzy matching (`rapidfuzz`) optional on `ItemSource`
- [ ] DRF read API — build only when an external consumer actually needs prices
- [ ] **Price-drop alerts / target price** — `target_price`/`notify_below` on `SearchableItem` or `ItemSource`, checked at the end of each scheduled run, with a simple notifier (log/email/webhook). Highest-value payoff of the "track pricing over time" goal; all prerequisites exist after Phase 3.
- [ ] **Result retention / dedup policy** — a daily scheduler storing every variant row on every run grows `SearchResult` quickly (rows × variants × days). Only insert when price/`instock` changed vs the last snapshot, or add a pruning/compaction command. Decide before the scheduler runs unattended long-term.
- [ ] **Scrape-health surfacing in the UI** — surface per-item/source freshness ("last successful update" column, failed-fetch indicator, "retry failed only" action) from existing `FetchJob`/`WebUpdate` data. Pairs well with price-drop alerts.
- [ ] **Secret handling for `Source.request_headers`/`request_body_template`** — fine for a single-user local tool, but auth tokens/cookies would be stored plaintext in the DB and rendered in a form; mask/segregate sensitive header values before any remote (Postgres/Gunicorn) deploy.

---

## 11. `search_scrape` Submodule

**Decision:** Keep as **separate repo** (git submodule at `search_scrape/`), scoped to **HTML parsing only**.

- **HTML-only:** the submodule owns `SearchParser` + the HTML DOM engine (`Element`, DOM stack, `check_element_*`/`read_*`) and HTML parsers like `CCSearchParser`. Its value is DOM traversal.
- **JSON stays in `tracking`:** `JSONSearchParser` and all JSON API parsers (`ShopifyParser`, `StorepassParser`, `WtFiltersParser`) live in `tracking/parsers.py` — JSON parsing shares almost none of the submodule's HTML machinery, so keeping it in the app avoids coupling and submodule friction.
- **Uniform contract in `tracking`:** the `parse_response(response)` shim for HTML parsers is a `tracking`-side mixin/adapter, so the submodule needs no changes for the JSON move.
- New **HTML** parsers still land in the submodule first; the Django registry stays in `tracking/parsers.py`.
- Consider pip packaging only if submodule friction becomes painful. If CC ever becomes the last HTML source, revisit whether to retire the submodule and vendor `CCSearchParser` into `tracking`.

---

## 12. Requirements Summary

Consolidated from original open questions (Jul 2026):

| Area | Requirement |
|------|-------------|
| Item types | Generic — MTG and GPUs equally |
| Users | Single-user, local/personal, no auth |
| Scale | Tens typical; up to 100–200; longer update runs acceptable |
| Updates | Manual button now; daily schedule later |
| Stored results | All parsed matches + search term; user picks relevance in UI |
| Out of stock | NULL price + stock flag (low priority) |
| Currency | CAD only |
| Second/third vendors | A Shopify vendor and a Storepass vendor; a POST-API vendor (Phase 4) |
| Site rendering | JS-rendered with JSON search APIs — parse JSON via `requests`; Playwright only if no JSON API (potential/unscheduled) |
| Custom sources | Developer-added parsers (user is developer) |
| Timezone | America/Halifax (hard-coded) |
| Job broker | Redis OK when background jobs needed |
| Submodule | Keep `search_scrape` separate |
| UI | Bootstrap + DataTables + Chart.js; desktop-only |

---

## 13. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07 | Generic app for MTG + GPUs | Avoid domain-specific models; rules on `ItemSource` |
| 2026-07 | Single-user, no auth | Personal/local tool; simplifies UI and models |
| 2026-07 | ≤200 items, sync updates OK | Manual refresh; delay loop sufficient for now |
| 2026-07 | Manual updates first | Scheduled daily scrape in Phase 3 |
| 2026-07 | Store all parsed results + search term | User judges relevance; better for ambiguous names |
| 2026-07 | NULL price for OOS (low priority) | Cleaner semantics than `price = 0` |
| 2026-07 | CAD only | All current sources are Canadian |
| 2026-07 | Phase 2 vendors: a Shopify vendor + a Storepass vendor | User preference; good MTG coverage |
| 2026-07 | **Parse vendor JSON search APIs, not HTML** | Investigations show products are JS-rendered but backed by fetchable JSON APIs (Shopify prod-indexer, Storepass, POST JSON apps); cleaner, stable data vs DOM scraping |
| 2026-07 | Phase 2 scope: GET APIs (Shopify + Storepass) | The Shopify and Storepass vendors return JSON via GET; simplest first step |
| 2026-07 | Parsers keyed by platform, `parser_key` decoupled from `Source` PK | Multiple vendors can share `shopify`/`storepass` parsers |
| 2026-07 | `parser_key` **required**; no fallback to `key` | No live data needs back-compat; `key` is purely a user-facing abbreviation, `parser_key` alone selects the parser (misconfig surfaces at form validation) |
| 2026-07 | POST APIs (the POST-JSON API) deferred to Phase 4 | Needs `Fetcher` POST + body template; not in initial scope |
| 2026-07 | Playwright is last resort (only if no JSON API) | JSON APIs remove the need for a browser for known vendors |
| 2026-07 | One `SearchResult` per variant/condition | Matches API shape; `ItemSource` patterns filter variant explosion |
| 2026-07 | Developer-maintained parsers | User is developer; UI-driven custom sources deferred |
| 2026-07 | `TIME_ZONE = America/Halifax` | User location; hard-coded is fine |
| 2026-07 | Redis for future job queue | Acceptable for remote deploy with Huey |
| 2026-07 | Keep `search_scrape` submodule, **scoped HTML-only** | Its value is the HTML DOM engine; reuse across projects; separate versioning |
| 2026-07 | JSON parsers + `JSONSearchParser` live in `tracking`, not the submodule | JSON parsing shares almost none of the HTML machinery; avoids coupling and submodule friction |
| 2026-07 | Bootstrap + DataTables + Chart.js | Current stack; desktop-only; HTMX optional later |

---

*Next step: Phase 2a — JSON parsing infrastructure (`JSONSearchParser`, `parse_response` refactor, `Fetcher` headers, `Source` fields).*
