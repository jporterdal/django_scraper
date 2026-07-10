# Phase 1: Step 5

## Goal

Implement roadmap item **#5**: `SearchResult.search_term`; persist all parsed rows per fetch.

**User-facing outcome:** Every row scraped from a vendor search page is stored as its own `SearchResult`, tagged with the exact search term that was queried. Summary views (latest price, sparklines) compute the **lowest in-stock price** at display time — they do not discard rows at ingest.

**Locked-in semantics** (from [plan.md](plan.md) §4.2):
- Store **all parsed result rows** from each fetch (not only `lowest_price()` / title-matched rows).
- Store **`search_term`** — the human-readable term sent to the vendor (i.e. `SearchableItem.text` at fetch time).
- “Best price” for summaries = `Min(price)` where `instock` indicates in-stock (and eventually `price IS NOT NULL`; nullable price is Phase 4 — out of scope here).

---

## Current state (do not redo)

| Area | Status |
|------|--------|
| `scrape.run_web_update()` loops `parser.results` and bulk-creates rows | ✅ Already stores all parsed rows |
| `search_term` field on `SearchResult` | ❌ Missing |
| `search_term` passed in `bulk_create` kwargs | ❌ Missing |
| List view / sparkline queries filter in-stock for summaries | ❌ Uses `Min("price")` on all rows |
| `latest_minprice` subquery | ⚠️ Fragile — three independent `Subquery(...[:1])` calls can mismatch title vs price on ties; missing `instock` filter once all rows are stored |

**Key files:**
- [`tracking/models.py`](tracking/models.py) — `SearchResult` model
- [`tracking/scrape.py`](tracking/scrape.py) — ingest loop (`kws.append({...})` ~line 129)
- [`tracking/views.py`](tracking/views.py) — `SearchableListView.get_context_data()` and `get_queryset()`
- [`tracking/tests.py`](tracking/tests.py) — extend `ScrapeOrchestratorTests` + add query tests
- [`tracking/admin.py`](tracking/admin.py) — optional `search_term` in `list_display`

**Environment:** Activate venv before Django commands:
```bash
source /home/ross/work/django_scraper/venv/bin/activate
cd /home/ross/work/django_scraper
```

---

## Out of scope (do NOT implement in this step)

- `SearchResult.price` NULL migration for out-of-stock (Phase 4 / low priority)
- `ItemSource` include/exclude title patterns (Phase 1 step 8 / Phase 2)
- Removing GPU-specific patterns from `CCSearchParser` (step 8)
- Item detail / full results table UI (Phase 2)
- `FetchJob` model (step 6)
- Changes to `search_scrape` submodule (unless a parser stops populating `results` — it should not)

---

## Implementation tasks

### Task 1 — Add `search_term` to `SearchResult`

**File:** `tracking/models.py`

Add field after `title` (or near other scrape metadata):

```python
search_term = models.CharField(
    max_length=125,
    verbose_name="Search term used for this fetch",
)
```

Use `max_length=125` to match `SearchableItem.text`.

**Migration:** Create `tracking/migrations/0004_searchresult_search_term.py` manually (avoid interactive `makemigrations`):

1. `AddField` with `default=""` and `preserve_default=False` **or** `default=""` + data migration.
2. `RunPython` backfill: for each existing `SearchResult`, set `search_term = item.text` via `select_related("item")`.
3. After backfill, alter field to `blank=False` if you used empty default — or add with non-empty default from the start using backfill in same migration.

**Recommended pattern:**
```python
# 1. Add nullable/blank field
# 2. RunPython backfill from item.text
# 3. AlterField blank=False (optional if added non-null with default="" first)
```

---

### Task 2 — Persist `search_term` in scrape orchestrator

**File:** `tracking/scrape.py`

In the `for result in parser.results:` loop, add to the dict passed to `bulk_create`:

```python
kws.append({
    "title": result["title"],
    "price": result["price"],
    "category": result["category"],
    "search_term": item.text,   # <-- ADD THIS
    "item": item,
    "instock": result["instock"],
    "source": source,
    "update": webupdate,
})
```

**Important:**
- Use `item.text` (the term actually queried), not `parser.term`, unless you have confirmed they always match — they should, but `item.text` is the canonical DB value.
- Store **every** entry in `parser.results` — do not call `parser.lowest_price()` or filter by `match_title()` before saving. Ingest is intentionally verbose; filtering is for display.

**Optional normalization (recommended):** Coerce `instock` to `1`/`0` when appending, since parsers may set bool but the model is `SmallIntegerField`:
```python
"instock": 1 if result["instock"] else 0,
```

---

### Task 3 — Fix summary queries to use in-stock rows only

**File:** `tracking/views.py` — `SearchableListView`

#### 3a. Price history sparklines (`get_context_data`)

Change:
```python
sr = SearchResult.objects.values("update", "item").annotate(
    lowest_price=Min("price"),
    ...
)
```

To filter in-stock first:
```python
sr = (
    SearchResult.objects.filter(instock=1)
    .values("update", "item")
    .annotate(
        lowest_price=Min("price"),
        timestamp=F("update__timestamp"),
    )
    .order_by("-timestamp")
)
```

If a snapshot has no in-stock rows, that update simply won't contribute a sparkline point — acceptable.

#### 3b. Latest price annotation (`get_queryset`)

Replace the broken subquery. **Do not use** `price=Min('price')` inside `.filter()`.

**Correct approach** — lowest in-stock price from latest `WebUpdate`:

```python
subq = (
    SearchResult.objects.filter(
        item=OuterRef("id"),
        update_id=latest_update_id,
        instock=1,
    )
    .order_by("price")
    .values("price")[:1]
)

title_subq = (
    SearchResult.objects.filter(
        item=OuterRef("id"),
        update_id=latest_update_id,
        instock=1,
        price=OuterRef("latest_minprice"),  # tie-break: title from cheapest row
    )
    .values("title")[:1]
)
```

**Simpler alternative** (only use if necessary, acceptable for MVP): annotate min price only; pick any matching title via subquery on `(item, update, price)` — or use two subqueries ordered by `price` and take first `price` + `title` from the same ordered queryset:

```python
cheapest = SearchResult.objects.filter(
    item=OuterRef("id"),
    update_id=latest_update_id,
    instock=1,
).order_by("price")

return queryset.annotate(
    latest_minprice=Subquery(cheapest.values("price")[:1]),
    latest_minprice_title=Subquery(cheapest.values("title")[:1]),
    latest_minprice_timestamp=Subquery(cheapest.values("update__timestamp")[:1]),
)
```

Using the same ordered `cheapest` queryset for all three subqueries ensures title matches the min-price row.

---

### Task 4 — Admin (minor)

**File:** `tracking/admin.py`

Add `search_term` to `SearchResultAdmin.list_display` and `search_fields` if useful for debugging.

---

### Task 5 — Tests

**File:** `tracking/tests.py`

Add/extend tests. Run with:
```bash
python manage.py test tracking -v 2
```

#### 5a. `test_stores_search_term_on_each_result`

Extend `ScrapeOrchestratorTests` (mock `_run_parser_search`, real DB writes):

- Mock parser with **2** results in `parser.results`.
- Assert `SearchResult.objects.count() == 2`.
- Assert every row has `search_term == self.item.text`.

#### 5b. `test_stores_all_parser_results_not_only_matched`

- Mock results with one in-stock and one out-of-stock row (different titles/prices).
- Assert **both** rows persisted (proves no `lowest_price()` filtering at ingest).

#### 5c. `test_latest_minprice_uses_in_stock_only`

- Create item + `WebUpdate` + `SearchResult` rows manually:
  - In-stock @ $100
  - Out-of-stock @ $1 (would wrongly win if not filtered)
- Hit `SearchableListView` / check annotated queryset:
  ```python
  item = SearchableListView.get_queryset(view)  # or client.get + parse, or direct queryset
  assert item.latest_minprice == 100
  ```
  Easiest: unit-test the queryset via `SearchableListView.as_view()` with `RequestFactory`, or create view instance and call `get_queryset()`.

#### 5d. Migration smoke test (optional)

- After migration, existing rows (if any in dev DB) have `search_term` populated — covered by backfill `RunPython`.

**Test fixtures:** Continue using `Source.objects.update_or_create(key="cc", ...)` in setUp — migration `0002` may already create a `cc` source.

---

## Verification checklist

Manual smoke test after implementation:

1. `python manage.py migrate`
2. `python manage.py test tracking`
3. `python manage.py runserver`
4. Ensure an item has an `ItemSource` linked to `cc` source.
5. Run **Update All Active** from `/view_terms/`.
6. In Django shell:
   ```python
   from tracking.models import SearchResult
   sr = SearchResult.objects.latest("id")
   assert sr.search_term  # non-empty
   # If CC returned multiple products, count > 1 per item per update is expected
   SearchResult.objects.filter(update=sr.update, item=sr.item).count()
   ```
7. Confirm list page still shows a sensible “Latest Price” (in-stock minimum, not an OOS junk price).

---

## Files to touch (summary)

| File | Action |
|------|--------|
| `tracking/models.py` | Add `search_term` field |
| `tracking/migrations/0004_*.py` | Schema + backfill migration |
| `tracking/scrape.py` | Include `search_term` in `kws`; optional `instock` coercion |
| `tracking/views.py` | Fix sparkline + `latest_minprice` queries (in-stock only; fix subquery) |
| `tracking/admin.py` | Show `search_term` (optional) |
| `tracking/tests.py` | 3+ new tests |
| `plan.md` | Check off step 5 when done (optional) |

**Do not modify** unless broken by this step:
- `search_scrape/` submodule
- Tag management UI
- `Fetcher` / rate limiting

---

## Design notes for the implementing agent

1. **Ingest vs display:** `parser.results` = all product cards on the search results page. `parser.lowest_price()` applies `match_title()` — that is for on-the-fly summaries only, not DB ingest. Step 5 explicitly rejects ingest-time filtering.

2. **`search_term` vs `item.text`:** They should be identical at fetch time. Storing `search_term` on each row preserves history if the user later edits `SearchableItem.text`.

3. **`instock` type:** Model uses `SmallIntegerField`; CC parser sets bool. Coercing at ingest avoids query bugs with `instock=1`.

4. **Volume:** Storing all rows may increase row count per update. This is expected and accepted (tens–200 items scale).

5. **No UI required for step 5:** Displaying the full results table per item is Phase 2. Only fix existing summary queries so they remain correct with multiple rows per fetch.

---

## Definition of done

- [x] `SearchResult.search_term` exists; migration applied; existing rows backfilled
- [x] Every `parser.results` entry creates one `SearchResult` with `search_term=item.text`
- [x] Sparkline and latest-price queries use **in-stock** rows only
- [x] `latest_minprice` uses one ordered subquery for price+title; filters `instock=1`
- [x] All `tracking` tests pass (`python manage.py test tracking`)
- [x] No changes to out-of-scope items above

---

# Phase 1: Dependency overview (steps 5–10)

Steps **5–10** can be assigned to separate agents, but some touch the same files. Use this matrix to avoid merge conflicts.

| Step | Primary files | Depends on | Safe parallel with |
|------|---------------|------------|-------------------|
| **5** | `models.py`, `scrape.py`, `views.py`, `tests.py` | Steps 1–4 (done) | 7, 8, 10 |
| **6** | `models.py`, `scrape.py`, `admin.py`, `tests.py` | Steps 1–4 (done); soft: **5** (same `scrape.py`) | 7, 8, 9, 10 if `scrape.py` merge coordinated |
| **7** | `settings.py`, `views.py`, templates | None | 5, 6, 8, 9, 10 |
| **8** | `requirements.txt`, docs | None | All |
| **9** | `models.py`, `parsers.py`, migration | None | 7, 8, 10; caution with **5** / **6** on `models.py` |
| **10** | `fixtures/`, `tests.py`, `parsers.py` (read-only) | None | 7, 8; caution with **9** if `parsers.py` edited |

**High-conflict pairs:** 5 + 6 (both edit `scrape.py`); 5 + 9 + 6 (all may edit `models.py`). Recommended order if sequential: **5 → 6 → 9 → 7 → 8 → 10**, but 7, 8, and 10 are fully independent if others’ merges are complete.

**Environment (all steps):**
```bash
source /home/ross/work/django_scraper/venv/bin/activate
cd /home/ross/work/django_scraper
python manage.py test tracking
```

---

# Phase 1: Step 6

## Goal

Implement roadmap item **#6**: `FetchJob` error capture.

**User-facing outcome:** Each per-item, per-source scrape attempt within a `WebUpdate` batch is recorded in the database — success or failure — with HTTP status, error message, and duration. Users can inspect failures without reading server logs (via admin or scrape history UI).

---

## Current state

| Area | Status |
|------|--------|
| Errors counted in `WebUpdateStats.error_count` | ✅ |
| Errors logged via `logging` in `scrape.py` | ✅ |
| Per-attempt DB record | ❌ |
| `WebUpdate` created only when first result succeeds | ⚠️ Failed-only batches leave no `WebUpdate` row |

**Key files:**
- [`tracking/scrape.py`](tracking/scrape.py) — orchestrator loop
- [`tracking/fetcher.py`](tracking/fetcher.py) — HTTP client (`get()` returns `response`)
- [`tracking/models.py`](tracking/models.py) — add `FetchJob`
- [`tracking/admin.py`](tracking/admin.py) — readonly job list
- [`tracking/templates/tracking/webupdate_list.html`](tracking/templates/tracking/webupdate_list.html) — optional error summary column
- [`tracking/tests.py`](tracking/tests.py)

---

## Dependencies

- **Requires:** Steps 1–4 complete (orchestrator exists).
- **Soft dependency on step 5:** If step 5 is in progress, coordinate on `scrape.py` / `models.py` merges.
- **Does not require:** Steps 7–10.

---

## Out of scope

- User-facing FetchJob CRUD UI (admin readonly is enough for MVP)
- Retry UI or automatic retries beyond existing `Fetcher` HTTP retries
- `WebUpdate.status` / `item_count` fields on `WebUpdate` (optional enhancement — not required)
- Replacing logging with DB-only error handling (keep both)

---

## Task 1 — Add `FetchJob` model

**File:** `tracking/models.py`

```python
class FetchJob(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        HTTP_ERROR = "http_error", "HTTP error"
        PARSE_ERROR = "parse_error", "Parse error"
        CONFIG_ERROR = "config_error", "Configuration error"
        EMPTY = "empty", "No results"

    webupdate = models.ForeignKey(
        WebUpdate,
        on_delete=models.CASCADE,
        related_name="fetch_jobs",
    )
    item = models.ForeignKey(SearchableItem, on_delete=models.CASCADE)
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    search_term = models.CharField(max_length=125)
    search_url = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    result_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["webupdate", "status"]),
        ]
        ordering = ["id"]
```

**Migration:** `tracking/migrations/0005_fetchjob.py` (number may vary if step 5 added `0004`).

---

## Task 2 — Create `WebUpdate` at batch start

**File:** `tracking/scrape.py`

At the beginning of `run_web_update()`, after building `item_sources` list:

```python
webupdate = WebUpdate.objects.create()
```

Remove lazy creation inside `for result in parser.results:` loop. Every batch gets a `WebUpdate` even if all fetches fail.

---

## Task 3 — Record a `FetchJob` for every attempt

Refactor the per-`item_source` loop to use a helper or structured try/finally:

```python
import time

def _record_fetch_job(webupdate, item, source, search_term, search_url, status, ...):
    FetchJob.objects.create(...)
```

**For each `item_source` iteration:**

1. Record `start = time.perf_counter()` at loop body start.
2. On each failure path (config error, KeyError, parse exception, HTTP non-200, empty results), create `FetchJob` with appropriate `status`, `http_status`, `error_message`, `duration_ms`.
3. On success (results stored), create `FetchJob` with `status=SUCCESS`, `result_count=len(parser.results)`, `http_status=200`.

**Refactor `_run_parser_search`** to return structured result instead of `bool`:

```python
@dataclass
class FetchOutcome:
    ok: bool
    http_status: int | None
    error_message: str
    result_count: int
```

Or return `(ok, http_status, error_message)` and count results on parser.

Capture HTTP status from `response.status_code` even on failure (403, 404, etc.).

**Truncate** `error_message` to ~2000 chars if storing exception text.

---

## Task 4 — Update `WebUpdateStats` (optional but useful)

Extend `WebUpdateStats` dataclass:

```python
@dataclass(frozen=True)
class WebUpdateStats:
    result_count: int
    error_count: int
    search_count: int
    fetch_job_count: int  # optional
```

No view changes required if `error_count` already drives user messages.

---

## Task 5 — Admin / scrape history UI

**File:** `tracking/admin.py`

```python
@admin.register(FetchJob)
class FetchJobAdmin(admin.ModelAdmin):
    list_display = ["webupdate", "item", "source", "status", "http_status", "result_count", "duration_ms"]
    list_filter = ["status", "source"]
    readonly_fields = [all fields]
    def has_add_permission(...): return False
    def has_change_permission(...): return False
```

**Optional — `webupdate_list.html`:** Add columns “Jobs” / “Errors” via annotation in `UpdateScheduleListView.get_queryset()`:

```python
.annotate(
    job_count=Count("fetch_jobs"),
    error_job_count=Count("fetch_jobs", filter=Q(fetch_jobs__status__ne="success")),
)
```

---

## Task 6 — Tests

**File:** `tracking/tests.py`

| Test | Assert |
|------|--------|
| `test_fetch_job_on_http_failure` | Mock non-200 → `FetchJob` with `HTTP_ERROR`, `WebUpdate` exists |
| `test_fetch_job_on_success` | Mock 2 results → `SUCCESS`, `result_count=2` |
| `test_fetch_job_on_unknown_parser` | `CONFIG_ERROR`, no HTTP call |
| `test_webupdate_created_even_if_all_fail` | All errors → `WebUpdate.objects.count() == 1` |

Update existing `ScrapeOrchestratorTests` that assert `WebUpdate.objects.count() == 1` on success — may now expect `WebUpdate` even on zero-result runs.

---

## Definition of done

- [x] `FetchJob` model + migration
- [x] `WebUpdate` created at batch start
- [x] One `FetchJob` row per `ItemSource` attempt (success and failure)
- [x] `duration_ms` and `http_status` populated where applicable
- [x] Admin readonly list for `FetchJob`
- [x] All `tracking` tests pass

---

# Phase 1: Step 7

## Goal

Implement roadmap item **#7**: `TIME_ZONE = "America/Halifax"`; format timestamps in templates for Atlantic display.

**User-facing outcome:** All datetimes shown in the UI reflect **Atlantic time** (Halifax), per [plan.md](plan.md) decision log. Storage remains UTC (`USE_TZ = True`).

---

## Current state

| Area | Status |
|------|--------|
| `TIME_ZONE` in settings | `'UTC'` |
| `USE_TZ` | `True` ✅ (keep) |
| Templates using `\|date` filter | `webupdate_list.html`, `searchableitem_list.html` |
| Python `strftime` in `views.py` for sparkline JSON | Uses naive UTC formatting in `get_context_data()` |

---

## Dependencies

- **Independent** of steps 5, 6, 8, 9, 10.
- **No migration** required.

---

## Out of scope

- Per-user timezone preference
- `django-environ` override for `TIME_ZONE` (hard-coded per plan)
- Changing DB storage format

---

## Task 1 — Update settings

**File:** `django_scraper/settings.py`

```python
TIME_ZONE = "America/Halifax"
```

Keep `USE_I18N = True` and `USE_TZ = True`.

---

## Task 2 — Fix Python-side date formatting in views

**File:** `tracking/views.py` — `SearchableListView.get_context_data()`

Replace:
```python
timestamp.strftime("%d/%m/%y")
```

With timezone-aware local formatting:
```python
from django.utils import timezone

local_ts = timezone.localtime(timestamp)
local_ts.strftime("%d/%m/%y")
```

Apply only when `timestamp` is not None.

---

## Task 3 — Audit all templates

| File | Current | Action |
|------|---------|--------|
| `searchableitem_list.html` | `latest_minprice_timestamp.date\|date:"Y-m-d"` | OK if `USE_TZ` + `TIME_ZONE` set — `\|date` uses active TZ |
| `webupdate_list.html` | `update.timestamp\|date:"Y-m-d H:i"` | OK — verify displays Atlantic after settings change |
| Other templates | — | Grep for `timestamp`, `date:`, `strftime` |

**Optional consistency:** Use a single format project-wide, e.g. `Y-m-d H:i T` or `Y-m-d g:i A` for 12-hour Atlantic.

---

## Task 4 — Tests

**File:** `tracking/tests.py`

```python
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import datetime

@override_settings(TIME_ZONE="America/Halifax", USE_TZ=True)
class TimezoneDisplayTests(TestCase):
    def test_localtime_used_in_sparkline_json(self):
        # Create WebUpdate + SearchResult at known UTC instant
        # Call SearchableListView.get_context_data()
        # Assert items_json date string matches Atlantic offset
```

Or simpler: `self.assertEqual(settings.TIME_ZONE, "America/Halifax")` plus one integration test on `view_terms` response containing expected local date string for a fixture timestamp.

---

## Verification

1. Set `TIME_ZONE = "America/Halifax"`.
2. Create a `WebUpdate` at a known UTC time (e.g. 2026-01-15 18:00 UTC = 14:00 AST).
3. Confirm `/view_updates/` shows `14:00` not `18:00`.

---

## Definition of done

- [x] `TIME_ZONE = "America/Halifax"` in settings
- [x] Sparkline JSON dates use `timezone.localtime()`
- [x] Template timestamps render in Atlantic (manual or automated check)
- [x] All tests pass

---

# Phase 1: Step 8

## Goal

Implement roadmap item **#8**: Add `django-environ` to `requirements.txt`.

---

## Current state

| Area | Status |
|------|--------|
| `django-environ` in `requirements.txt` | ✅ Already present (`django-environ==0.14.0`) |
| `settings.py` imports `environ` | ✅ Already used |
| `.env_sample` | ✅ Exists |

**This step may already be complete.** Agent should verify and document; only add work if something is missing.

---

## Dependencies

- **Fully independent** — no code coupling to other steps.

---

## Task 1 — Verify requirements

**File:** `requirements.txt`

Ensure a pinned line exists:
```
django-environ==0.14.0
```
(or current compatible version — match what `pip freeze` shows in venv).

If missing, add it and run:
```bash
pip install -r requirements.txt
```

---

## Task 2 — Verify settings integration

**File:** `django_scraper/settings.py`

Confirm:
- `import environ`
- `environ.Env.read_env(...)` loads `.env_sample` or `.env`
- `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS` read from env
- Scrape settings (`SCRAPE_REQUEST_DELAY_SECONDS`, etc.) use `env.float` / `env.int`

No functional change expected if already wired.

---

## Task 3 — Verify fresh install

In a clean venv:
```bash
python -m venv /tmp/test-venv && source /tmp/test-venv/bin/activate
pip install -r requirements.txt
python manage.py check
```

---

## Task 4 — Documentation (minimal)

**File:** `README.md` (optional, one paragraph)

Note that config lives in `.env` (copy from `.env_sample`) and `django-environ` loads it. Only add if README is still a stub.

---

## Definition of done

- [x] `django-environ` pinned in `requirements.txt`
- [x] `python manage.py check` passes on fresh `pip install -r requirements.txt`
- [x] No duplicate/unpinned `environ` dependency elsewhere
- [ ] If already done: mark step complete in `plan.md` with no unnecessary code churn

---

# Phase 1: Step 9

## Goal

Implement roadmap item **#9**: Remove GPU-specific patterns from `CCSearchParser`; stub `ItemSource` pattern fields.

**User-facing outcome:** Parser code is **generic** (no MSI/ASUS/Gigabyte hardcoding). `ItemSource` has database fields for future per-item title include/exclude rules (wired in Phase 2 UI / filtering).

---

## Current state

**File:** `tracking/parsers.py` — GPU-specific patterns in `_init_vars()`:

```python
self.title_patterns.extend([
    "msi.*" + self.term.lower() + ".*",
    "asus.*" + self.term.lower() + ".*",
    "gigabyte.*" + self.term.lower() + ".*",
])
```

`SearchParser` base already adds `term$` pattern via `title_patterns = [self.term.lower() + "$"]` in `_init_vars()`.

**`ItemSource`:** has `url_suffix` only — no pattern fields yet.

---

## Dependencies

- **Independent** of steps 6, 7, 8, 10.
- **Soft:** Step 5 may add ingest logic; step 9 does **not** require filtering at ingest (stub only).
- **Phase 2** will add UI forms for patterns — do not build full CRUD here.

---

## Out of scope

- ItemSource management UI (Phase 2)
- Applying patterns to filter `parser.results` at ingest (Phase 2 — optional helper stub OK)
- Removing `search_scrape/example.py` duplicate GPU patterns (submodule — optional note only)
- `match_title()` changes in submodule

---

## Task 1 — Add pattern fields to `ItemSource`

**File:** `tracking/models.py`

```python
title_include_patterns = models.JSONField(
    default=list,
    blank=True,
    verbose_name="Regex patterns; result title must match at least one if non-empty",
)
title_exclude_patterns = models.JSONField(
    default=list,
    blank=True,
    verbose_name="Regex patterns; matching titles are excluded",
)
```

**Migration:** `0006_itemsource_title_patterns.py` (number may vary).

Validate in `clean()` or form layer later; for stub, empty list = no extra filtering.

---

## Task 2 — Remove GPU patterns from `CCSearchParser`

**File:** `tracking/parsers.py`

Delete the `self.title_patterns.extend([...])` block entirely. `_init_vars()` should only call `super()._init_vars()`.

Parser matching at parse time uses default `term$` anchor only. Disambiguation is deferred to user review of stored results (step 5) and future `ItemSource` rules (Phase 2).

---

## Task 3 — Stub helper for future filtering (optional)

**File:** `tracking/matching.py` (new, small)

```python
import re

def title_matches_rules(title: str, include_patterns: list, exclude_patterns: list) -> bool:
    """Return True if title passes include/exclude rules. Empty lists = pass."""
    if exclude_patterns and any(re.search(p, title, re.I) for p in exclude_patterns):
        return False
    if include_patterns and not any(re.search(p, title, re.I) for p in include_patterns):
        return False
    return True
```

**Do not call from `scrape.py` yet** unless product owner wants early filtering — stub file + unit tests only.

---

## Task 4 — Admin

**File:** `tracking/admin.py`

Add `title_include_patterns` and `title_exclude_patterns` to `ItemSourceAdmin` fields as readonly or editable JSON — editable in admin is OK for developer-user until Phase 2 UI exists.

---

## Task 5 — Tests

| Test | Assert |
|------|--------|
| `test_cc_parser_no_gpu_patterns` | After `_init_vars()`, `title_patterns` contains only `term$` style default |
| `test_item_source_pattern_fields_default_empty` | New `ItemSource` has `[]` for both JSON fields |
| `test_title_matches_rules_stub` | If helper added: include/exclude logic works |

---

## Definition of done

- [x] `title_include_patterns` / `title_exclude_patterns` on `ItemSource` + migration
- [x] GPU vendor regexes removed from `CCSearchParser`
- [x] Optional `matching.py` stub with tests
- [x] All tests pass
- [x] No Phase 2 UI required

---

# Phase 1: Step 10

## Goal

Implement roadmap item **#10**: Parser HTML fixture test for `CCSearchParser`.

**User-facing outcome:** Regression safety — CC parser changes are tested against saved HTML so DOM selector drift is caught in CI/local `manage.py test`.

---

## Current state

| Area | Status |
|------|--------|
| `tracking/fixtures/html/` | ❌ Does not exist |
| Parser tests | Mock-based orchestrator tests only; no real HTML parse test |
| `CCSearchParser` | In `tracking/parsers.py` |

---

## Dependencies

- **Independent** of steps 5–9 (tests parser in isolation).
- **Soft:** Step 9 edits `parsers.py` — run step 10 after 9 or merge carefully.
- **Does not require** network access or live CC scraping in tests.

---

## Out of scope

- VCR/live HTTP tests against canadacomputers.com
- Submodule `search_scrape/example.py` tests
- Face to Face parser fixtures (Phase 2)
- Updating submodule `SearchParser` base class

---

## Task 1 — Create fixture directory and sample HTML

**Directory:** `tracking/fixtures/html/cc/`

**File:** `tracking/fixtures/html/cc/search_results_minimal.html`

Build **minimal synthetic HTML** matching `CCSearchParser` selectors (stable, no network):

```html
<!DOCTYPE html>
<html><body>
  <div class="product">
    <div class="product-title"><a>Test GPU RTX 5070</a></div>
    <span class="price">$799.99</span>
    <div class="available-tag"><b>In Store - Available for Pickup</b></div>
  </div>
  <div class="product">
    <div class="product-title"><a>Other Product</a></div>
    <span class="price">$99.00</span>
    <div class="available-tag"><b>Out of Stock</b></div>
  </div>
</body></html>
```

**Optional second fixture:** `search_results_multi_product.html` with 3+ products for “store all rows” assertions (pairs with step 5).

**Optional:** Save a **sanitized snippet** from a real CC page (strip scripts, PII) as `search_results_live_sample.html` — mark in README comment as manually refreshed.

---

## Task 2 — Test module

**File:** `tracking/tests/test_cc_parser.py` **or** class in `tracking/tests.py`

```python
from pathlib import Path
from django.test import SimpleTestCase
from tracking.parsers import CCSearchParser

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "html" / "cc"

class CCSearchParserFixtureTests(SimpleTestCase):
    def _parse_fixture(self, filename, term="RTX 5070"):
        html = (FIXTURES / filename).read_text()
        parser = CCSearchParser(term=term)
        parser._init_vars()
        parser.feed(html)
        return parser

    def test_parses_products_from_minimal_fixture(self):
        parser = self._parse_fixture("search_results_minimal.html")
        self.assertEqual(len(parser.results), 2)

    def test_parses_price_and_title(self):
        parser = self._parse_fixture("search_results_minimal.html")
        first = parser.results[0]
        self.assertEqual(first["title"], "Test GPU RTX 5070")
        self.assertAlmostEqual(first["price"], 799.99)
        self.assertTrue(first["instock"])

    def test_out_of_stock_product(self):
        parser = self._parse_fixture("search_results_minimal.html")
        oos = parser.results[1]
        self.assertFalse(oos["instock"])
```

Use `SimpleTestCase` if test needs no DB — faster.

---

## Task 3 — Fixture path resolution

Use path relative to test file or `settings.BASE_DIR`:

```python
from django.conf import settings
FIXTURES = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "cc"
```

Ensure fixture files are committed to git.

---

## Task 4 — CI / discoverability

Tests run via existing command:
```bash
python manage.py test tracking
```

No new test runner required. If splitting `tests/test_cc_parser.py`, ensure `tracking/tests/` is a package (`__init__.py`) or Django discovers `tests.py` only — prefer single `tests.py` class if project has no test package yet.

**Current project:** tests live in `tracking/tests.py` only — add `CCSearchParserFixtureTests` class there unless splitting is desired.

---

## Task 5 — Document fixture maintenance

Add a short comment at top of fixture file or in test class docstring:

> When Canada Computers changes search result HTML structure, update fixture and `CCSearchParser` selectors together.

---

## Definition of done

- [x] `tracking/fixtures/html/cc/` with at least one HTML fixture
- [x] Tests parse fixture without HTTP
- [x] Assertions on `len(parser.results)`, price, title, `instock`
- [x] `python manage.py test tracking` passes
- [x] No network calls in parser fixture tests

