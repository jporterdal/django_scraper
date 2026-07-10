# Phase 2 Subplans — Face to Face Games + Disambiguation

> Agent-ready implementation plans for [plan.md](plan.md) Phase 2.  
> **Prerequisite:** Phase 1 complete (models, scrape orchestrator, tags, `search_term`, `FetchJob`, `ItemSource` pattern field stubs, CC parser fixtures).

**Environment (all steps):**
```bash
source /home/ross/work/django_scraper/venv/bin/activate
cd /home/ross/work/django_scraper
python manage.py test tracking
```

**Agent rule:** When implementing a step, **only** edit this file under that step’s **Definition of done** subsection (check items with `[x]`). Do not modify other steps’ sections.

---

# Phase 2: Dependency overview

| Step | Summary | Depends on | Safe parallel with |
|------|---------|------------|-------------------|
| **1** | Investigate F2F search page (HTML vs JS) | Phase 1 | **5** (Source UI only, no F2F parser) |
| **2** | `FaceToFaceParser` + F2F `Source` row | **Step 1** (decision + fixture) | 3, 4 after 1 done |
| **3** | Pattern fields in user forms + apply matching | Phase 1 (`matching.py`, JSON fields exist) | 4, **5** (coordinate ItemSource UI) |
| **4** | Item detail / history page | Phase 1 (`SearchResult.search_term`) | 3, 5 |
| **5** | Source + ItemSource management UI | Phase 1 (`Source`, `ItemSource` models) | 1, 3, 4 (coordinate `urls.py`, `views.py`, templates) |

**High-conflict files:** `tracking/views.py`, `tracking/urls.py`, `tracking/templates/`, `tracking/parsers.py` (step 2), `tracking/scrape.py` (step 3 if ingest filtering added).

**Recommended order if sequential:** 1 → 2 → 5 (Source CRUD) → 3 (patterns on ItemSource forms) → 4 (detail page).  
**Recommended async batches:**
- Batch A: Step 1 + Step 5 (Source list/CRUD without F2F)
- Batch B: Step 2 (after 1)
- Batch C: Steps 3 + 4 in parallel

---

# Phase 2: Step 1

## Goal

Implement roadmap item: **Investigate F2F search page (HTML vs JS — determines parser approach)**.

**Outcome:** A written decision record + saved HTML fixture(s) so step 2 can implement `FaceToFaceParser` without guesswork. No production parser code in this step.

**Target site:** [Face to Face Games](https://facetofacegames.com) — Canadian MTG retailer (user’s Phase 2 vendor choice).

---

## Current state

| Area | Status |
|------|--------|
| F2F parser | ❌ |
| F2F HTML fixtures | ❌ (`tracking/fixtures/html/cc/` exists for CC only) |
| Playwright / JS fetch | ❌ Deferred to Phase 4 |

---

## Dependencies

- **Requires:** Phase 1 complete; network access for manual fetch.
- **Blocks:** Step 2 (parser implementation).
- **Does not block:** Steps 3, 4, 5.

---

## Out of scope

- Implementing `FaceToFaceParser` (step 2)
- Playwright integration (Phase 4)
- Adding F2F `Source` database row (step 2)

---

## Task 1 — Discover search URL pattern

1. Manually search on facetofacegames.com for a known card name (e.g. `Lightning Bolt`).
2. Record the resulting URL in the browser address bar.
3. Derive a `base_search_url` template with `{term}` placeholder, e.g.:
   - `https://facetofacegames.com/search?q={term}`
   - (Actual path/query params **must be verified** — do not copy this example blindly.)
4. Confirm `{term}` is URL-encoded by `Source.build_search_url()` (`quote_plus`) — note if site expects `+` vs `%20`.

**Deliverable:** URL template documented in investigation file (Task 4).

---

## Task 2 — Determine HTML vs JS rendering

For the search results page, check:

| Signal | Server-rendered HTML | JS-rendered |
|--------|---------------------|-------------|
| `curl` / `requests.get` returns product cards with prices | ✅ Likely HTML | ❌ Empty shell |
| View source shows prices in static HTML | ✅ | ❌ |
| Products appear only after browser JS runs | ❌ | ✅ |
| Heavy React/Vue root div with little content in source | ❌ | ✅ |

**Procedure:**
```bash
# From project venv — polite single request
python -c "
import requests
url = '...'  # F2F search URL for a test term
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 ...'})
print(r.status_code, len(r.text))
open('/tmp/f2f_search.html', 'w').write(r.text)
"
```

Inspect `/tmp/f2f_search.html` for product title/price markup.

**Decision output:**
- **`HTML_PARSER`** — extend `SearchParser` in `tracking/parsers.py` (like CC)
- **`BEAUTIFULSOUP`** — prototype in `tracking/parsers.py` with `bs4` (add dep if chosen)
- **`PLAYWRIGHT_DEFERRED`** — JS required; document blockers; step 2 may stub parser + skip live tests until Phase 4

---

## Task 3 — Document DOM selectors (if HTML available)

For each data field the app needs, record:

| Field | CSS/class/tag hint | Notes |
|-------|-------------------|-------|
| Product container | e.g. `.product-card` | One row per search hit |
| Title | | |
| Price | CAD format | |
| In stock | | |

Compare structure to `CCSearchParser` methods: `check_within_item_object`, `check_element_title`, `check_element_price`, `check_element_instock`, `read_*`.

Note ambiguities (multiple printings, foil badges, etc.) — relevant for step 3 pattern fields.

---

## Task 4 — Write investigation document

**File:** `tracking/docs/f2f_investigation.md` (create `tracking/docs/` if needed)

Sections:
1. Search URL template (with `{term}`)
2. Rendering decision (`HTML_PARSER` / `BEAUTIFULSOUP` / `PLAYWRIGHT_DEFERRED`)
3. Sample request headers (User-Agent)
4. DOM selector map
5. Sample product count for test query
6. Risks (rate limiting, bot detection, pagination)
7. Recommended parser base class for step 2

---

## Task 5 — Save HTML fixture(s)

**Directory:** `tracking/fixtures/html/f2f/`

| File | Purpose |
|------|---------|
| `search_results_sample.html` | Sanitized full page or main results fragment from Task 2 |
| `README.md` (optional) | How/when fixture was captured; refresh instructions |

- Strip scripts if storing full page (optional, reduces noise).
- Do **not** commit cookies/session tokens.
- Fixture will be used by step 2 parser tests.

---

## Task 6 — Tests (minimal)

This step is research-only. Optional smoke test:

```python
# tracking/tests/test_f2f_investigation.py
def test_f2f_fixture_exists():
    path = settings.BASE_DIR / "tracking/fixtures/html/f2f/search_results_sample.html"
    assert path.exists()
    assert len(path.read_text()) > 500
```

---

## Definition of done

- [x] F2F search URL template with `{term}` documented
- [x] HTML vs JS decision recorded in `tracking/docs/f2f_investigation.md`
- [x] DOM selector notes for title, price, stock, container
- [x] At least one fixture in `tracking/fixtures/html/f2f/`
- [x] Step 2 recommendation stated (parser approach)
- [x] No `FaceToFaceParser` or F2F `Source` row added yet

---

# Phase 2: Step 2

## Goal

Implement roadmap item: **`FaceToFaceParser` + `Source` row for F2F**.

**Outcome:** Users can link items to an `f2f` source; scrape orchestrator fetches F2F search pages and stores parsed results alongside CC.

---

## Current state

| Area | Status |
|------|--------|
| `sources` registry | `{'cc': CCSearchParser}` only |
| `Source.key` | `CharField(max_length=3)` — `f2f` fits |
| F2F investigation | Expected from step 1 |

---

## Dependencies

- **Requires:** Step 1 complete (`f2f_investigation.md`, fixture, URL template, rendering decision).
- **Soft:** If step 1 concludes `PLAYWRIGHT_DEFERRED`, implement parser against fixture only; document that live scrape may fail until Phase 4.
- **Parallel:** Steps 3, 4, 5 can proceed independently.

---

## Out of scope

- ItemSource pattern UI (step 3)
- Item detail page (step 4)
- Source management UI (step 5) — use migration/data migration for F2F `Source` row
- Submodule changes unless investigation recommends extending `search_scrape` base class

---

## Task 1 — Add F2F constants

**File:** `tracking/models.py` (alongside `CC_DEFAULT_SEARCH_URL`)

```python
F2F_DEFAULT_SEARCH_URL = "..."  # From f2f_investigation.md — must contain {term}
```

---

## Task 2 — Implement `FaceToFaceParser`

**File:** `tracking/parsers.py`

Follow step 1 selector map. Pattern mirrors `CCSearchParser`:

```python
class FaceToFaceParser(SearchParser):
    def _init_vars(self):
        super()._init_vars()
        # No URL here — URL comes from Source.base_search_url via scrape.py
        # No vendor-specific title_patterns — use ItemSource patterns (step 3)

    def check_within_item_object(self, element): ...
    def check_element_title(self, elt=None): ...
    def check_element_price(self, elt=None): ...
    def read_price(self, data): ...  # CAD $ format may differ from CC
    def check_element_instock(self, elt=None): ...
    def read_instock(self, data): ...
```

**If BeautifulSoup chosen:** parser may expose `parse_html(html: str)` called from a thin wrapper; keep registry interface consistent with scrape’s `_run_parser_search` → `parser.feed()`.

**Register:**
```python
sources = {
    'cc': CCSearchParser,
    'f2f': FaceToFaceParser,
}
```

Add helper:
```python
def get_parser_choices():
    return [(k, k) for k in sources]
```

---

## Task 3 — Data migration for F2F Source

**File:** `tracking/migrations/0007_source_f2f.py` (number may vary)

```python
def add_f2f_source(apps, schema_editor):
    Source = apps.get_model("tracking", "Source")
    if not Source.objects.filter(key="f2f").exists():
        Source.objects.create(
            key="f2f",
            name="Face to Face Games",
            base_search_url=F2F_DEFAULT_SEARCH_URL,
        )
```

---

## Task 4 — Parser fixture tests

**File:** `tracking/tests.py` (append `FaceToFaceParserFixtureTests`)

- Parse `tracking/fixtures/html/f2f/search_results_sample.html`
- Assert `len(parser.results) >= 1`
- Assert price/title/instock on at least one row
- No HTTP in tests

Mirror `CCSearchParserFixtureTests` from Phase 1 step 10.

---

## Task 5 — Integration smoke test (optional, network)

Manual or marked `@skipUnless` test:
- One live F2F fetch for a stable search term
- Skip in CI by default

---

## Task 6 — Verify end-to-end

1. In shell/admin: create `ItemSource(item=..., source_id='f2f')`
2. Run update for that item
3. Confirm `SearchResult` rows with `source_id='f2f'` and `FetchJob` SUCCESS

---

## Definition of done

- [ ] `FaceToFaceParser` in `tracking/parsers.py` registered as `f2f`
- [ ] `F2F_DEFAULT_SEARCH_URL` + migration creates `Source(key='f2f', ...)`
- [ ] Fixture-based parser tests pass
- [ ] `python manage.py test tracking` passes
- [ ] Investigation doc referenced in parser module docstring or `f2f_investigation.md` link

---

# Phase 2: Step 3

## Goal

Implement roadmap item: **`ItemSource` include/exclude pattern fields + admin/forms**.

**Outcome:** Users can configure per-item, per-source title include/exclude regex patterns through the **web UI** (not admin). Summaries and charts respect patterns when computing “relevant” prices.

---

## Current state

| Area | Status |
|------|--------|
| `ItemSource.title_include_patterns` / `title_exclude_patterns` | ✅ JSONField on model (migration `0006`) |
| `tracking/matching.py` → `title_matches_rules()` | ✅ Stub with tests |
| Pattern filtering in `scrape.py` | ❌ Not wired |
| User-facing pattern forms | ❌ |
| Admin | ✅ Fields on `ItemSourceAdmin` (developer fallback) |

---

## Dependencies

- **Requires:** Phase 1 (`matching.py`, JSON fields).
- **Soft:** Step 5 (ItemSource assignment UI) — **coordinate**: pattern fields belong on ItemSource create/edit forms. Can implement form widgets here and embed in step 5 templates, or step 5 imports shared form.
- **Does not require:** Step 2 (F2F), step 4.

---

## Design decision — store all vs filter at ingest

**Locked (Phase 1):** Store **all** parsed `SearchResult` rows.

**Step 3 approach:** Apply `title_matches_rules()` at **display/summary time**, not by dropping rows at ingest:

| Layer | Behavior |
|-------|----------|
| Ingest (`scrape.py`) | Store all parser results (unchanged) |
| List view `latest_minprice` | When `ItemSource` has patterns, compute min over in-stock rows **matching rules** for that item+source |
| Item detail (step 4) | Show all rows; highlight or badge rows that match/mismatch patterns |
| Sparklines | Use pattern-filtered min per update (join through `ItemSource`) |

Document in code comments. Do **not** silently delete non-matching rows from DB.

---

## Out of scope

- Fuzzy matching (`rapidfuzz`) — Phase 4
- Pinned result URL — Phase 4
- JSON pattern editing in Django admin as primary UX (keep admin as fallback only)

---

## Task 1 — `ItemSourcePatternForm` / validation

**File:** `tracking/forms.py` (new)

```python
class ItemSourceForm(forms.ModelForm):
    class Meta:
        model = ItemSource
        fields = ["source", "url_suffix", "title_include_patterns", "title_exclude_patterns"]

    def clean_title_include_patterns(self):
        # Ensure list of strings; compile each regex to catch invalid patterns
        ...

    def clean_title_exclude_patterns(self):
        ...
```

Validate each pattern with `re.compile(p)` — raise `ValidationError` on invalid regex.

**UX:** Use textarea with **one pattern per line** (convert to/from JSON list in `clean` / `__init__`).

---

## Task 2 — Helper for queryset filtering

**File:** `tracking/matching.py`

```python
def result_matches_item_source(result_title: str, item_source: ItemSource) -> bool:
    return title_matches_rules(
        result_title,
        item_source.title_include_patterns or [],
        item_source.title_exclude_patterns or [],
    )
```

Optional: `filter_results_for_item_source(queryset, item_source)` for reuse in views.

---

## Task 3 — Apply patterns in list view summaries

**File:** `tracking/views.py` — `SearchableListView`

Current `cheapest` subquery uses all in-stock rows for latest update. **Enhancement options:**

**Option A (simpler MVP):** Keep list view as global min in-stock; patterns only on detail page.

**Option B (full):** Annotate in Python in `get_context_data` for displayed items only (acceptable at ≤200 items scale).

**Option C (SQL-heavy):** Defer — document as future optimization.

**Recommend Option A for MVP** unless agent can implement B cleanly. Document choice in step 3 completion notes.

If Option B: for each item, load `ItemSource` rows, filter `SearchResult` for latest update per source through `result_matches_item_source`, then take min price.

---

## Task 4 — User-facing forms (coordination with step 5)

Minimum deliverable if step 5 not done yet:

- **URL:** `/edit_term/<pk>/sources/` — list ItemSources for item
- **URL:** `/edit_term/<pk>/sources/add/` — `ItemSourceForm`
- **URL:** `/item_source/<pk>/edit/` — edit patterns + url_suffix

If step 5 implements the same routes, **share** `ItemSourceForm` and avoid duplication.

**Form fields exposed:**
- `source` (dropdown of `Source.objects.all()`)
- `url_suffix`
- `title_include_patterns` (textarea, one regex per line)
- `title_exclude_patterns` (textarea)

Help text with MTG examples:
- Include: `Lightning Bolt`, `.*Near Mint.*`
- Exclude: `Foil`, `Japanese`

---

## Task 5 — Tests

| Test | Assert |
|------|--------|
| `test_form_rejects_invalid_regex` | ValidationError |
| `test_form_accepts_line_separated_patterns` | Saved as JSON list |
| `test_result_matches_item_source` | Include/exclude logic |
| `test_empty_patterns_pass_all` | True for any title |

---

## Definition of done

- [ ] `ItemSourceForm` with regex validation (newlines → JSON list)
- [ ] User-facing route(s) to add/edit ItemSource patterns (or shared with step 5)
- [ ] `result_matches_item_source()` helper in `matching.py`
- [ ] Pattern-aware summary behavior documented (Option A or B implemented)
- [ ] Ingest still stores all parsed rows
- [ ] Tests pass

---

# Phase 2: Step 4

## Goal

Implement roadmap item: **Item detail page — all results table + per-source Chart.js history**.

**Outcome:** “View History” on the item list works. Users see every stored `SearchResult` for an item and a per-source price history chart.

---

## Current state

| Area | Status |
|------|--------|
| “View History” button | Disabled placeholder in `searchableitem_list.html` |
| Item detail view/URL | ❌ |
| Chart.js | ✅ Used for sparklines on list page |

---

## Dependencies

- **Requires:** Phase 1 (`SearchResult.search_term`, `instock`, multiple rows per fetch).
- **Soft:** Step 3 — pattern match highlighting on detail table (optional enhancement; can show all rows without badges first).
- **Soft:** Step 2 — multiple sources make charts more interesting but CC-only is fine for testing.
- **Parallel:** Steps 3, 5.

---

## Out of scope

- Export CSV/JSON (Phase 3)
- Full ECharts migration (plan optional)
- Editing item fields on detail page (keep Edit separate)

---

## Task 1 — URL and view

**File:** `tracking/urls.py`

```python
path("item/<int:pk>/", views.SearchableItemDetailView.as_view(), name="item_detail"),
```

**File:** `tracking/views.py`

```python
class SearchableItemDetailView(DetailView):
    model = SearchableItem
    template_name = "tracking/searchableitem_detail.html"
    context_object_name = "item"
```

---

## Task 2 — Context data

**`get_context_data` should provide:**

1. **`results`** — `SearchResult.objects.filter(item=self.object).select_related('source', 'update').order_by('-update__timestamp', 'source__key', 'price')`

2. **`results_by_update`** (optional) — grouped for display

3. **`chart_data_json`** — structure for Chart.js:
   ```json
   {
     "cc": {"labels": ["2026-01-15", ...], "prices": [9.99, ...]},
     "f2f": {...}
   }
   ```
   Per source: for each `WebUpdate`, lowest in-stock price among results matching optional patterns (step 3) or all in-stock rows.

4. **`item_sources`** — `ItemSource.objects.filter(item=self.object).select_related('source')` for pattern context

5. **`tags`** — item.tags.all() for header display

Use `timezone.localtime()` for chart labels (step 7 / Phase 1).

---

## Task 3 — Template `searchableitem_detail.html`

**Layout (Bootstrap, match existing pages):**

| Section | Content |
|---------|---------|
| Header | Item text, priority, active badge, tags |
| Nav | Back to list, Edit item, Manage sources (step 5 link if exists) |
| Charts | One card per source with `<canvas id="chart-{source_key}">` |
| Table | All `SearchResult` rows: timestamp, source, search_term, title, price, instock, category |
| Optional | Row CSS class if `result_matches_item_source()` (step 3) |

**Table columns (minimum):**
- Date/time (Atlantic, `|date:"Y-m-d H:i"`)
- Source
- Search term
- Title
- Price (CAD `$`)
- In stock (yes/no badge)

**Chart:** Line chart per source — lowest in-stock price per `WebUpdate` (pattern-aware if step 3 done).

Reuse Chart.js CDN from list template.

---

## Task 4 — Wire list page link

**File:** `searchableitem_list.html`

Replace disabled View History:
```html
<a href="{% url 'item_detail' item.pk %}" class="btn btn-info btn-sm">View History</a>
```

---

## Task 5 — Tests

| Test | Assert |
|------|--------|
| `test_item_detail_200` | Create item + results → GET returns 200 |
| `test_item_detail_lists_all_results` | Multiple SearchResults visible |
| `test_item_detail_chart_data` | Context includes per-source series when results exist |
| `test_list_page_history_link` | `href` points to `item_detail` |

Use `Client` + fixtures.

---

## Definition of done

- [ ] `SearchableItemDetailView` + template
- [ ] URL `item/<pk>/` named `item_detail`
- [ ] Table shows all `SearchResult` rows for item
- [ ] Per-source Chart.js line chart (lowest in-stock per update)
- [ ] “View History” enabled on list page
- [ ] Tests pass

---

# Phase 2: Step 5

## Goal

Implement roadmap item: **Source + ItemSource management UI**.

**Outcome:** Users add/edit/delete price sources with `{term}` URL templates and link sources to items — without Django admin.

---

## Current state

| Area | Status |
|------|--------|
| `Source` model + `build_search_url()` | ✅ |
| `ItemSource` model | ✅ |
| User Source UI | ❌ (admin only) |
| Tag management UI | ✅ Pattern to follow (`tag_list.html`, etc.) |
| Parser registry | `parsers.sources` — keys must match `Source.key` |

---

## Dependencies

- **Requires:** Phase 1 models.
- **Soft:** Step 2 adds `f2f` source — UI should list all `Source` rows dynamically.
- **Overlap with step 3:** ItemSource forms with pattern fields — **share** `ItemSourceForm` from step 3 if both implemented; if step 5 runs alone, include pattern textareas now or stub empty fields.
- **Parallel:** Step 1, 4.

---

## Out of scope

- User-defined parsers / DOM selectors (plan: developer-maintained parsers)
- Expanding `Source.key` beyond 3 chars (optional follow-up migration)
- Deleting admin entirely

---

## Task 1 — Source CRUD views

**File:** `tracking/views.py`  
**Pattern:** Mirror `TagListView`, `TagCreateView`, `TagUpdateView`, `TagDeleteView`.

| URL | Name | View |
|-----|------|------|
| `/sources/` | `view_sources` | `SourceListView` |
| `/sources/add/` | `add_source` | `SourceCreateView` |
| `/sources/<str:pk>/edit/` | `edit_source` | `SourceUpdateView` |
| `/sources/<str:pk>/delete/` | `delete_source` | `SourceDeleteView` |

Note: `Source.key` is PK (`str:pk` in URL).

**List queryset:** annotate `item_count=Count('itemsource')` (related name from FK).

---

## Task 2 — `SourceForm`

**File:** `tracking/forms.py`

```python
class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = ["name", "key", "base_search_url"]

    def clean_base_search_url(self):
        url = self.cleaned_data["base_search_url"]
        if "{term}" not in url:
            raise ValidationError("URL template must contain {term}")
        return url

    def clean_key(self):
        key = self.cleaned_data["key"]
        if key not in parsers.sources:
            # Warn or block — recommend block on create:
            raise ValidationError(
                f"No parser registered for key {key!r}. "
                f"Available: {', '.join(parsers.sources)}"
            )
        return key
```

**`key` field on edit:** Readonly (PK cannot change) or disallow edit view for key.

**Parser dropdown on create:** Populate from `parsers.sources.keys()` instead of free text — prevents CONFIG_ERROR at scrape time.

**Help text on `base_search_url`:**
> Example: `https://facetofacegames.com/search?q={term}` — `{term}` is replaced with the URL-encoded search query.

**Preview (optional):** Show `build_search_url("example card")` on edit page.

---

## Task 3 — Source templates

| Template | Based on |
|----------|----------|
| `source_list.html` | `tag_list.html` |
| `source_form.html` | `tag_form.html` |
| `source_confirm_delete.html` | `tag_confirm_delete.html` |

List columns: name, key, URL (truncated), parser registered ✓, # items, actions.

Delete confirmation: show `ItemSource` count, warn that `SearchResult` / `FetchJob` history for this source will cascade delete.

---

## Task 4 — ItemSource management

**Option A — Under item edit flow (recommended):**

| URL | Purpose |
|-----|---------|
| `/item/<pk>/sources/` | `ItemSourceListView` — sources linked to item |
| `/item/<pk>/sources/add/` | Add `ItemSource` |
| `/item_source/<int:pk>/edit/` | Edit suffix + patterns |
| `/item_source/<int:pk>/delete/` | Remove link |

**Option B — Under source edit flow:**

Show linked items on `source_form.html` with add/remove.

**Implement Option A** (matches item-centric workflow). Link from item detail (step 4) and item list Edit area.

**Form:** `ItemSourceForm` (step 3) with `source` dropdown excluding already-linked sources on add.

**Unique constraint:** Add `class Meta: unique_together = [('item', 'source')]` on `ItemSource` if not present — migration if needed.

---

## Task 5 — Navigation

**File:** `searchableitem_list.html` header:

```html
<a href="{% url 'view_sources' %}" class="btn btn-outline-primary btn-sm">Manage Sources</a>
```

Cross-links: source list → add source; item sources → back to item detail/list.

---

## Task 6 — Default sources via migration only

Do not hardcode CC/F2F creation in views. Step 2 migration adds F2F; `0002` added CC.

Users may add **additional** sources only for keys with registered parsers.

---

## Task 7 — Tests

| Test | Assert |
|------|--------|
| `test_source_list` | 200, contains CC source |
| `test_create_source_valid` | POST with valid template |
| `test_create_source_rejects_missing_term` | Form error |
| `test_create_source_rejects_unknown_parser_key` | Form error |
| `test_delete_source_cascade_warning` | Confirm page shows counts |
| `test_add_item_source` | Item linked to source |
| `test_duplicate_item_source_blocked` | unique_together |

---

## Task 8 — Admin policy

Keep `SourceAdmin` / `ItemSourceAdmin` for developer fallback or remove from primary workflow. Document in code comment that user UI is canonical.

---

## Definition of done

- [ ] Source list/create/edit/delete user pages
- [ ] `SourceForm` validates `{term}` and parser key
- [ ] ItemSource list/add/edit/delete under item (or equivalent)
- [ ] `unique_together` on (item, source) enforced
- [ ] Navigation from item list to Manage Sources
- [ ] Pattern + url_suffix editable on ItemSource (shared form with step 3)
- [ ] Tests pass
- [ ] Django admin not required for normal source/item-source management

---

# Phase 2: Completion checklist (all steps)

When all steps are done:

- [ ] F2F investigation documented + fixture saved (step 1)
- [ ] F2F parser registered + Source row (step 2)
- [ ] ItemSource patterns editable in UI + matching helper used in summaries/detail (step 3)
- [ ] Item detail page with full results + charts (step 4)
- [ ] Source + ItemSource CRUD without admin (step 5)
- [ ] `python manage.py test tracking` — all pass
- [ ] Update [plan.md](plan.md) Phase 2 checkboxes (optional, outside agent scope unless instructed)
