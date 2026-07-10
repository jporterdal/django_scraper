# Phase 2 Subplans — JSON API vendors (Shopify + Storepass) + disambiguation

> Agent-ready implementation plans for [plan.md](plan.md) **Phase 2 — JSON API vendors (Shopify + Storepass) + disambiguation**.
> The prior HTML/DOM version of these subplans is archived in [phase02_subplans_old.md](phase02_subplans_old.md) and is **superseded** — do not follow it.
> **Prerequisite:** Phase 1 complete (models, scrape orchestrator, `Fetcher`, tags, `SearchResult.search_term`, `FetchJob`, `ItemSource` pattern JSON fields, `matching.title_matches_rules()`, CC HTML fixture tests). Phase 2 Step 1 investigations are **done**: `tracking/docs/{f2f,hfx,wt}_investigation.md` and JSON fixtures `tracking/fixtures/html/{f2f,hfx,wt}/search_results_sample.json` already exist.

---

## Agent rules (read before editing)

1. **Edit only your own step's section.** When you implement "Phase 2: Step X", the **only** part of *this file* you may edit is that step's **Definition of done** checklist (tick `[x]`). Do not touch other steps' sections or their checkboxes.
2. **Environment — use exactly this in every step** (activate venv, set cwd, run the suite):
   ```bash
   source /home/ross/work/django_scraper/venv/bin/activate
   cd /home/ross/work/django_scraper
   python manage.py test tracking
   ```
3. **No network in tests.** All parser tests read saved JSON/HTML fixtures. Never make live HTTP calls in `python manage.py test tracking`.
4. **Respect dependencies** (see overview). If a step you depend on is not yet merged, either wait or stub against the documented interface, and say so in your completion notes.
5. **Migrations:** run `python manage.py makemigrations tracking` and commit the generated file. If two steps add migrations concurrently and numbers collide, fix the `dependencies = [...]` list so both chain off `0007` (Step 1) rather than duplicating a number.
6. **Keep the `search_scrape` submodule HTML-only.** All JSON parsers and the JSON base class live in `tracking/` (per plan.md §5.1/§11). Do not add JSON code to the submodule.
7. **Result dict contract (all parsers):** each entry in `parser.results` is `{"title": str, "price": float, "category": str, "instock": 0|1}`. The orchestrator (`scrape.run_web_update`) reads exactly these keys.

---

## Phase 2: Dependency overview

Steps map to plan.md Phase 2 bullets:

| Step | Summary | plan.md bullet | Depends on | Safe to run in parallel with |
|------|---------|----------------|------------|------------------------------|
| **1** | `Source` model: JSON-API config fields + migration | 2a (Source model) | Phase 1 | 5, 6 |
| **2** | Parser abstraction: `JSONSearchParser` + `parse_response` contract + orchestrator/`Fetcher` refactor | 2a (base class, parse_response, Fetcher headers) | **Step 1** | 5, 6 |
| **3** | `ShopifyParser` (Shopify vendor) + `Source` row + tests | 2b (Shopify) | **Steps 1, 2** | **4**, 5, 6 |
| **4** | `StorepassParser` (Storepass vendor) + `Source` row + tests | 2b (Storepass) | **Steps 1, 2** | **3**, 5, 6 |
| **5** | `ItemSource` include/exclude pattern form + matching in summaries | 2c (patterns) | Phase 1 | 1, 2, 3, 4, 6 (coordinate `forms.py` with 7) |
| **6** | Item detail page (all results table + per-source charts) | 2c (detail page) | Phase 1 | 1, 2, 3, 4, 5 |
| **7** | Source + ItemSource management UI | 2c (source UI) | **Step 1** | 3, 4, 6 (coordinate `forms.py` with 5) |

**Recommended sequential order:** 1 → 2 → (3 ∥ 4) → 5 → 7 → 6.

**Recommended async batches:**
- **Batch A:** Step 1 (unblocks most infra) — plus Step 5 and Step 6 can start in parallel (they only need Phase 1).
- **Batch B:** Step 2 (after Step 1).
- **Batch C:** Steps 3 and 4 in parallel (after Step 2).
- **Batch D:** Step 7 (after Step 1; coordinate `forms.py` with Step 5).

**High-conflict files (coordinate / rebase carefully):**
- `tracking/models.py` + `tracking/migrations/` — Steps 1, 3, 4 (chain migrations off `0007`).
- `tracking/scrape.py`, `tracking/fetcher.py` — Step 2 only.
- `tracking/parsers.py` — Steps 2, 3, 4 (2 adds base class; 3/4 add subclasses + registry entries — append, don't rewrite).
- `tracking/forms.py` — Steps 5 and 7 (Step 5 owns `ItemSourceForm`; Step 7 owns `SourceForm` and imports `ItemSourceForm`).
- `tracking/views.py`, `tracking/urls.py`, `tracking/templates/tracking/` — Steps 5, 6, 7 (append URLs/views; new templates per feature).
- `tracking/tests.py` — every step appends its own `TestCase` classes; Step 2 must also **update** two existing assertions (documented in Step 2).

---

# Phase 2: Step 1

## Goal

Extend the `Source` model with the configuration JSON-API parsers need: a parser selector decoupled from the primary key, per-source HTTP headers, an optional page size, and wider `key`/`base_search_url` columns. Ship the migration (with data backfill) so later steps can add `shopify`/`storepass` sources.

**plan.md bullet:** Phase 2 §2a — *"`Source` model: add `parser_key` (registry selector, decoupled from PK), `request_headers` (JSON), optional `page_size`; widen `key`/`base_search_url`; migration."*

## Dependencies

- **Requires:** Phase 1.
- **Blocks:** Steps 2, 3, 4, 7.
- **Does not block:** Steps 5, 6.

## Current state

- `Source` (in `tracking/models.py`): `name`, `key` (`CharField(max_length=3, primary_key=True)`), `base_search_url` (`CharField(max_length=500)`), `build_search_url()`.
- `key` is currently **both** the PK **and** the parser-registry lookup (`scrape.run_web_update` does `parsers.sources[source.key]`).
- Latest migration: `0006_itemsource_title_patterns`.

## Tasks

### Task 1 — Add fields to `Source` (`tracking/models.py`)

Add to the `Source` model:

```python
parser_key = models.CharField(
    max_length=20,
    blank=True,
    default="",
    verbose_name="Parser registry key (e.g. 'shopify', 'storepass'); blank falls back to 'key'",
)
request_headers = models.JSONField(
    default=dict,
    blank=True,
    verbose_name="Extra HTTP headers sent with search requests (e.g. Accept/Origin/Referer)",
)
page_size = models.PositiveSmallIntegerField(
    null=True,
    blank=True,
    verbose_name="Optional results-per-page hint for paginated APIs (used in Phase 3)",
)
```

Widen the existing columns:
- `key`: `max_length=3` → `max_length=20` (keep `primary_key=True`).
- `base_search_url`: `max_length=500` → `max_length=1000` (Storepass URLs are long).

> **Design revision (Jul 2026, post-implementation):** `parser_key` is now a **required** field
> (`blank=False`, no default) and there is **no fallback to `key`**. `key` is purely a user-facing
> abbreviation; `parser_key` alone selects the parser (misconfiguration surfaces at form
> validation via Step 7's required dropdown). The `effective_parser_key` property described below
> was removed; the orchestrator uses `source.parser_key` directly. This is safe because the
> project has no live data needing back-compat. Migration `0011` alters `parser_key` to required.

### Task 2 — Migration

Run `makemigrations` (creates `0007_...`) with the `AddField`s + `AlterField`s for the new/widened columns. (The original plan included a `RunPython` backfill of `parser_key` from `key`; with the design revision above `parser_key` is required and set explicitly per source, so no fallback/backfill semantics are relied upon.)

### Task 3 — Admin

In `tracking/admin.py`, update `SourceAdmin.list_display` to `["key", "parser_key", "name", "base_search_url"]` (developer visibility; user-facing UI is Step 7).

## Tests (append to `tracking/tests.py`)

Add `class SourceJSONConfigTests(TestCase)`:

| Test | Assert |
|------|--------|
| `test_new_fields_have_expected_defaults` | fresh `Source(...)` has `request_headers == {}`, `page_size is None` |
| `test_parser_key_is_required_on_form` | `SourceForm` with blank `parser_key` is invalid (design revision: required, no fallback) |
| `test_key_accepts_longer_value` | a `Source` with `key="storepass"` (len 9) saves without error |

Do not break existing `SourceModelTests` (they construct `Source(key="cc", ...)` — still valid).

## Definition of done

- [x] `parser_key`, `request_headers`, `page_size` added; `key`/`base_search_url` widened
- [x] `parser_key` is required (no fallback to `key`); orchestrator uses `source.parser_key` directly (design revision; migration `0011`)
- [x] Migration `0007_*` created for the new/widened columns
- [x] `SourceAdmin.list_display` shows `parser_key`
- [x] New tests pass; `python manage.py test tracking` green

---

# Phase 2: Step 2

## Goal

Introduce a JSON parser base class and a **uniform parse contract** so the orchestrator handles HTML and JSON parsers identically, wire per-source request headers through the `Fetcher`, and switch parser lookup to `parser_key`. This is the infrastructure that Steps 3 and 4 build parsers against.

**plan.md bullet:** Phase 2 §2a — *"Add `JSONSearchParser` base class in `tracking`… Uniform `parse_response(response)` contract; refactor `scrape._run_parser_search`… HTML `SearchParser` gets a `parse_response` wrapper via a `tracking`-side mixin/adapter… `Fetcher`: merge per-source `request_headers`."*

## Dependencies

- **Requires:** Step 1 (`Source.request_headers`, `Source.parser_key`).
- **Blocks:** Steps 3, 4.
- **Does not block:** Steps 5, 6, 7.

## Current state (interfaces you are changing)

- `tracking/parsers.py`: `CCSearchParser(SearchParser)` + `sources = {'cc': CCSearchParser}`. `SearchParser` (submodule) is HTML-only; `_init_vars()` + `feed(html)`.
- `tracking/scrape.py::_run_parser_search(parser, fetcher, url)` does: `parser._init_vars(); parser.url = url; response = fetcher.get(url); ...; parser.feed(response.text)`.
- `tracking/scrape.py::run_web_update` looks up `parsers.sources[source.key]`.
- `tracking/fetcher.py::Fetcher.get(self, url)` — GET only, no per-request headers.
- `tracking/views.py::poll` calls `_run_parser_search(parser, Fetcher.from_settings(), search_url)`.

## Tasks

### Task 1 — `JSONSearchParser` base + HTML adapter (`tracking/parsers.py`)

Add (keep `search_scrape` untouched):

```python
class JSONSearchParser:
    """Base class for JSON API search parsers. Subclasses implement parse_data()."""
    data_keys = ["category", "title", "price", "instock"]

    def __init__(self, term=""):
        self.term = term
        self.url = None
        self.results = []

    def _init_vars(self):
        self.results = []

    def parse_response(self, response):
        self._init_vars()
        self.parse_data(response.json())

    def parse_data(self, data):
        raise NotImplementedError

    def add_result(self, title, price, instock, category=""):
        self.results.append({
            "title": str(title),
            "price": float(price),
            "instock": 1 if instock else 0,
            "category": category or "",
        })


class HTMLResponseParserMixin:
    """Gives submodule HTML parsers the uniform parse_response(response) contract."""
    def parse_response(self, response):
        self._init_vars()
        self.feed(response.text)
```

Make the CC parser use the mixin (MRO: mixin first):

```python
class CCSearchParser(HTMLResponseParserMixin, SearchParser):
    ...  # existing body unchanged
```

### Task 2 — `Fetcher` per-request headers (`tracking/fetcher.py`)

Change `get` to accept optional headers merged over the session (session `User-Agent` still applies unless overridden):

```python
def get(self, url, headers=None):
    logger.info("GET %s", url)
    response = self._session.get(url, timeout=self.timeout, headers=headers)
    ...  # existing status logging unchanged
    return response
```

### Task 3 — Orchestrator refactor (`tracking/scrape.py`)

`_run_parser_search`: use the uniform contract and pass headers.

```python
def _run_parser_search(parser, fetcher, url, headers=None):
    response = fetcher.get(url, headers=headers)
    if response.status_code != 200:
        return FetchOutcome(ok=False, http_status=response.status_code,
                            error_message=f"HTTP {response.status_code}", result_count=0)
    parser.parse_response(response)
    return FetchOutcome(ok=True, http_status=200, error_message="", result_count=len(parser.results))
```

In `run_web_update`:
- parser lookup → `parser_cls = parsers.sources[source.parser_key]` (keep the `KeyError` → `CONFIG_ERROR` handling; the error message may keep showing `source.key`).
- build headers → `headers = source.request_headers or None` and pass to the call: `outcome = _run_parser_search(parser, fetcher, search_url, headers=headers)`.

### Task 4 — Update the `poll` view (`tracking/views.py`)

`poll` still works with the default `headers=None`; no signature change needed there. Verify it runs (CC parser now has `parse_response`).

## Tests

### Update existing (required — signature changed)

- `ScrapeUrlIntegrationTests.test_passes_built_url_to_parser_search`: the call is now
  `mock_run_parser.assert_called_once_with(mock_parser, self.fetcher, expected_url, headers=None)`.

(Other orchestrator tests patch `_run_parser_search` or use `MagicMock` parsers and remain valid; `test_fetch_job_on_http_failure` returns before `parse_response` because status is 403.)

### Add new (`class ParserContractTests(SimpleTestCase)` + orchestrator header test)

| Test | Assert |
|------|--------|
| `test_json_parser_parse_response_populates_results` | a tiny `JSONSearchParser` subclass, fed a `MagicMock(json=lambda: {...})`, yields `results` with the 4 keys and `price` is a `float` |
| `test_add_result_coerces_types` | `add_result(title=1, price="3.5", instock=True)` → `{"title":"1","price":3.5,"instock":1,"category":""}` |
| `test_cc_parser_has_parse_response` | `CCSearchParser` instance parses a fake HTML response via `parse_response` (reuse the CC fixture; wrap in `MagicMock(text=...)`) |
| `test_run_web_update_passes_request_headers` | give the CC `Source` `request_headers={"Accept":"application/json"}`; patch `_run_parser_search`; assert it was called with `headers={"Accept":"application/json"}` |

## Definition of done

- [x] `JSONSearchParser` + `HTMLResponseParserMixin` in `tracking/parsers.py`; `CCSearchParser` uses the mixin
- [x] `Fetcher.get` accepts and forwards `headers`
- [x] `_run_parser_search` calls `parser.parse_response(response)` and forwards `headers`
- [x] `run_web_update` looks up `source.parser_key` and passes `source.request_headers`
- [x] Existing `test_passes_built_url_to_parser_search` updated; new contract/header tests added
- [x] `python manage.py test tracking` green

---

# Phase 2: Step 3

## Goal

Implement `ShopifyParser` for a Shopify `prod-indexer` JSON API, register it under `shopify`, and add the Shopify vendor's `Source` row. One `SearchResult` per variant/condition.

**plan.md bullet:** Phase 2 §2b — *"`ShopifyParser` (`prod-indexer`): `hits.hits[]._source` → one row per `variants[]`… register `shopify`."* Plus the Shopify vendor's `Source` row (created via the Source-management UI; see feedback.md Pass 1).

## Dependencies

- **Requires:** Step 1 (`parser_key`), Step 2 (`JSONSearchParser`, `parse_response`, orchestrator).
- **Parallel with:** Step 4 (append separate registry entries + separate migration).

## Reference (verified against the existing fixture)

Fixture: `tracking/fixtures/html/f2f/search_results_sample.json`. Shape: `hits.hits[]._source` with:
- `title` — full listing title (e.g. `Lightning Bolt [401] [...] [Foil]`)
- `MTG_Set_Name` — set (use as `category`; fall back to `Set` or `""`)
- `variants[]` — each has `price` (float), `inventoryQuantity` (int), `selectedOptions[]` (item with `name == "Condition"`, `value` like `NM`)

See `tracking/docs/f2f_investigation.md`.

## Tasks

### Task 1 — Base search URL (example only)

> **Superseded:** `Source` rows are created via the Source-management UI, not a committed
> migration or a per-vendor constant (see feedback.md Pass 1). No `*_DEFAULT_SEARCH_URL`
> constant is added for this vendor. A Shopify-style GET search URL template entered in the
> Source form looks like:

```python
# Example base_search_url (entered in the Source form, not committed to code):
# https://example.com/search?q={term}
```

### Task 2 — `ShopifyParser` (`tracking/parsers.py`)

```python
class ShopifyParser(JSONSearchParser):
    """Shopify prod-indexer (Elasticsearch-style) JSON search results."""
    def parse_data(self, data):
        for hit in data.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            title = src.get("title", "")
            category = src.get("MTG_Set_Name") or src.get("Set") or ""
            for variant in src.get("variants", []):
                condition = ""
                for opt in variant.get("selectedOptions", []):
                    if opt.get("name") == "Condition":
                        condition = opt.get("value", "")
                display = f"{title} ({condition})" if condition else title
                self.add_result(
                    title=display,
                    price=variant.get("price", 0),
                    instock=variant.get("inventoryQuantity", 0) > 0,
                    category=category,
                )
```

Register (append to the dict): `sources = {'cc': CCSearchParser, 'shopify': ShopifyParser}` (merge with Step 4's `storepass` entry — don't clobber).

### Task 3 — `Source` row (via the Source-management UI)

> **Superseded:** `Source` rows are created at runtime via the Source-management UI, not a
> committed data migration (see feedback.md Pass 1). The values below are an illustrative
> example of what to enter for a Shopify vendor:

```python
Source.objects.update_or_create(
    key="<key>",
    defaults={
        "name": "<Shopify vendor>",
        "parser_key": "shopify",
        "base_search_url": "https://example.com/search?q={term}",
        "request_headers": {"Accept": "application/json"},
    },
)
```

## Tests (append `class ShopifyParserFixtureTests(SimpleTestCase)`)

Load the fixture and call `parse_data(json.loads(...))` (or wrap in a fake response and call `parse_response`):

| Test | Assert |
|------|--------|
| `test_parses_at_least_one_variant_row` | `len(parser.results) >= 1` |
| `test_row_shape` | first row has float `price`, non-empty `title` containing `Lightning Bolt`, `instock in (0,1)` |
| `test_condition_in_title` | at least one row's title contains a condition tag like `(NM)` |
| `test_instock_derived_from_inventory` | a variant with `inventoryQuantity > 0` → `instock == 1` |

## Definition of done

- [x] `ShopifyParser` implemented and registered as `shopify`
- [x] Superseded: no `*_DEFAULT_SEARCH_URL` constant committed — the URL template is entered via the Source form
- [x] Superseded: the Shopify vendor's `Source` row is created via the Source-management UI (`parser_key="shopify"`, `Accept: application/json` header), not a committed migration
- [x] Fixture tests pass (no HTTP)
- [x] `python manage.py test tracking` green

---

# Phase 2: Step 4

## Goal

Implement `StorepassParser` for a Storepass SaaS JSON API, register it under `storepass`, and add the Storepass vendor's `Source` row. One `SearchResult` per condition variant.

**plan.md bullet:** Phase 2 §2b — *"`StorepassParser`: `products[]` → one row per `variantInfo[]`… register `storepass`; `store_id`/`product_line` carried in `base_search_url`."*

## Dependencies

- **Requires:** Step 1 (`parser_key`, `request_headers`), Step 2 (`JSONSearchParser`, orchestrator).
- **Parallel with:** Step 3 (append separate registry entry + separate migration).

## Reference (verified against the existing fixture)

Fixture: `tracking/fixtures/html/hfx/search_results_sample.json`. Shape: top-level `products[]` with:
- `display_name` (preferred) / `name`
- `variantInfo[]` — each has `price` (number; may be `int`), `inventory_quantity` (int), `title` (condition, e.g. `Near Mint`)
- `productLineData` (dict, optional) for set/category metadata

See `tracking/docs/hfx_investigation.md`.

## Tasks

### Task 1 — Base search URL (example only)

> **Superseded:** `Source` rows are created via the Source-management UI, not a committed
> migration or a per-vendor constant (see feedback.md Pass 1). No `*_DEFAULT_SEARCH_URL`
> constant is added for this vendor. A Storepass-style GET search URL template entered in the
> Source form looks like:

```python
# Example base_search_url (entered in the Source form, not committed to code):
# https://storepass.example/saas/search?store_id=<STORE_ID>&name={term}&limit=30&sort=Relevance&mongo=true&override_buylist_gt_price=true&product_line=Magic: the Gathering
```

`product_line` is overridable per item via `ItemSource.url_suffix` (e.g. `&product_line=Pokemon`).

### Task 2 — `StorepassParser` (`tracking/parsers.py`)

```python
class StorepassParser(JSONSearchParser):
    """Storepass SaaS JSON search results."""
    def parse_data(self, data):
        for product in data.get("products", []):
            title = product.get("display_name") or product.get("name", "")
            pld = product.get("productLineData")
            category = pld.get("set", "") if isinstance(pld, dict) else ""
            for variant in product.get("variantInfo", []):
                condition = variant.get("title", "")
                display = f"{title} ({condition})" if condition else title
                self.add_result(
                    title=display,
                    price=variant.get("price", 0),
                    instock=variant.get("inventory_quantity", 0) > 0,
                    category=category,
                )
```

Register (append): add `'storepass': StorepassParser` to `sources` (merge with Step 3's `shopify` entry).

### Task 3 — `Source` row (via the Source-management UI)

> **Superseded:** `Source` rows are created at runtime via the Source-management UI, not a
> committed data migration (see feedback.md Pass 1). Storepass expects the storefront's
> `Origin`/`Referer`. The values below are an illustrative example for a Storepass vendor:

```python
Source.objects.update_or_create(
    key="<key>",
    defaults={
        "name": "<Storepass vendor>",
        "parser_key": "storepass",
        "base_search_url": "https://storepass.example/saas/search?store_id=<STORE_ID>&name={term}&limit=30&sort=Relevance&mongo=true&override_buylist_gt_price=true&product_line=Magic: the Gathering",
        "request_headers": {
            "Accept": "application/json",
            "Origin": "https://example.com",
            "Referer": "https://example.com/",
        },
    },
)
```

> **Note:** Steps 3 and 4 no longer add committed data migrations to seed vendor `Source`
> rows (superseded by feedback.md Pass 1 — rows are created via the Source-management UI). Any
> schema migrations still chain off `0007` (Step 1).

## Tests (append `class StorepassParserFixtureTests(SimpleTestCase)`)

| Test | Assert |
|------|--------|
| `test_parses_at_least_one_variant_row` | `len(parser.results) >= 1` |
| `test_price_is_float` | every row's `price` is a `float` (guards int→float coercion; fixture has `price: 3`) |
| `test_condition_in_title` | at least one title contains a condition like `(Near Mint)` |
| `test_out_of_stock_variant` | a variant with `inventory_quantity == 0` → `instock == 0` |

## Definition of done

- [x] `StorepassParser` implemented and registered as `storepass`
- [x] Superseded: no `*_DEFAULT_SEARCH_URL` constant committed — the URL template is entered via the Source form
- [x] Superseded: the Storepass vendor's `Source` row is created via the Source-management UI (`parser_key="storepass"`, Accept/Origin/Referer headers), not a committed migration
- [x] Fixture tests pass (no HTTP)
- [x] `python manage.py test tracking` green

---

# Phase 2: Step 5

## Goal

Let users configure per-item, per-source **include/exclude title patterns** through a form, validate the regex, and make price summaries pattern-aware. Provides the shared `ItemSourceForm` used by Step 7.

**plan.md bullet:** Phase 2 §2c — *"`ItemSource` include/exclude pattern fields in user forms + apply matching (now central: filters NM/PL/Foil/set variant explosion)."*

## Dependencies

- **Requires:** Phase 1 only (`ItemSource.title_include_patterns`/`title_exclude_patterns` JSON fields, `matching.title_matches_rules()`).
- **Coordinate with Step 7:** Step 5 **owns** `tracking/forms.py::ItemSourceForm`. Step 7 imports it. If Step 7 lands first, it must not define a competing `ItemSourceForm`.
- **Independent of:** Steps 1–4, 6.

## Design decision (locked in plan.md §4.2/§4.3)

Store **all** parsed rows at ingest. Apply patterns only at **display/summary** time. Never delete non-matching `SearchResult` rows.

## Tasks

### Task 1 — Matching helper (`tracking/matching.py`)

Add (reuses the existing `title_matches_rules`):

```python
def result_matches_item_source(result_title, item_source):
    return title_matches_rules(
        result_title,
        item_source.title_include_patterns or [],
        item_source.title_exclude_patterns or [],
    )
```

### Task 2 — `ItemSourceForm` (`tracking/forms.py`, new file)

- `ModelForm` on `ItemSource`, fields: `source`, `url_suffix`, `title_include_patterns`, `title_exclude_patterns`.
- Present the two pattern fields as **textareas, one regex per line**; convert to/from JSON lists in `__init__` (list → newline-joined string for display) and `clean_*` (splitlines → list, dropping blanks).
- Validate each pattern with `re.compile(p)`; raise `forms.ValidationError` on invalid regex, naming the bad pattern.
- Add Bootstrap `form-control` widget classes (mirror `TagCreateView.get_form`).
- Help text with MTG examples — include: `Lightning Bolt`; exclude: `Foil`, `Japanese`.

Because `title_include_patterns`/`title_exclude_patterns` are `JSONField`, override the form fields as `CharField(widget=forms.Textarea, required=False)` and do the JSON<->text conversion yourself.

### Task 2b — Example patterns (drive help text + tests)

**Matching semantics** (from `matching.title_matches_rules`): each pattern is a Python regex evaluated with `re.search(..., re.I)` — so matches are **case-insensitive** and match **anywhere** in the title (not anchored). A title is kept when it matches **at least one include** (if any includes are set) **and matches no exclude**. Excludes win over includes.

**Why patterns matter now:** JSON parsers emit **one row per variant/condition**, and titles carry the printing/condition/finish. From the fixtures, a single "Lightning Bolt" search yields titles like:

- Shopify vendor (`ShopifyParser` appends the condition): `Lightning Bolt [401] [Rulebook Showcase] [Commander Legends: Battle for Baldur's Gate] [Foil] (NM)`
- Storepass vendor (`StorepassParser` appends the condition): `Lightning Bolt [Beatdown] (Near Mint)`
- CC (GPU, HTML): `MSI GeForce RTX 5070 Ti Gaming Trio OC 16GB`

So a user narrows that noise with patterns. Realistic examples:

| Item (search term) | Goal | Include patterns (one per line) | Exclude patterns (one per line) |
|--------------------|------|---------------------------------|---------------------------------|
| `Lightning Bolt` | Any near-mint copy, no foils/other languages | `\(NM\)`  `\(Near Mint\)` | `Foil`  `Japanese`  `\bJP\b` |
| `Lightning Bolt` | Pin one specific printing | `Beatdown` (or `\[Beatdown\]`) | `Foil`  `Signed`  `Altered`  `Proxy` |
| `Lightning Bolt` | Exclude played/damaged conditions | *(leave empty = allow all)* | `\(HP\)`  `Heavily Played`  `Damaged` |
| `RTX 5070` | Base 5070 only — **not** the "Ti" | `RTX 5070` | `\bTi\b`  `SUPER` |
| `RTX 5070` | One brand, new only | `MSI.*5070` | `Open Box`  `Refurb`  `Used` |

**Notes to surface in the form help text:**
- Plain words work as substrings: `Foil` matches `[Foil]`. Bracket characters are regex metacharacters, so to match a literal bracket use `\[Beatdown\]` (or just `Beatdown`).
- Condition tags differ by source — the Shopify vendor uses short codes (`(NM)`, `(PL)`, `(HP)`), the Storepass vendor uses full names (`(Near Mint)`, `(Lightly Played)`). Include both forms (e.g. `\(NM\)` and `\(Near Mint\)`) if the item is tracked on both.
- Use `\b...\b` word boundaries to avoid accidental substring hits (e.g. `\bTi\b` so `Ti` doesn't match inside `Trio`/`Titan`).
- Leaving **include empty** allows everything; **exclude** then just trims unwanted rows.

Use the `RTX 5070` / `\bTi\b` case and the `Lightning Bolt` / `Foil` case as fixtures in the Task 5 tests.

### Task 3 — Apply patterns in the list summary (`tracking/views.py`)

`SearchableListView.get_queryset` currently annotates `latest_minprice` via a pure-SQL subquery over in-stock rows. Choose and **document in a code comment**:

- **Option A (MVP, recommended):** leave the list-page global min as-is; apply pattern filtering only on the detail page (Step 6). Add a comment referencing this decision.
- **Option B (full):** for the displayed page of items, load their `ItemSource` rows and compute, in Python, the min in-stock price among latest-update results that pass `result_matches_item_source`. Acceptable at ≤200 items.

Pick A unless you can implement B cleanly; state which in your completion notes.

### Task 4 — Minimal edit route (only if Step 7 not yet merged)

If Step 7's ItemSource UI isn't available, add a thin edit view + URL so patterns are editable without admin:
- `path("item_source/<int:pk>/edit/", views.ItemSourceUpdateView.as_view(), name="edit_item_source")` using `ItemSourceForm`, template mirrors `tag_form.html`, success → `view_terms`.
If Step 7 is present, skip this (Step 7 wires the routes) and just ensure the shared form exists.

## Tests (append `class ItemSourceFormTests(TestCase)` + `class ResultMatchesItemSourceTests(TestCase)`)

| Test | Assert |
|------|--------|
| `test_form_rejects_invalid_regex` | `ItemSourceForm` with `title_include_patterns="[["` is invalid |
| `test_form_converts_lines_to_json_list` | posting two lines saves `title_include_patterns == ["a", "b"]` |
| `test_form_blank_patterns_save_empty_list` | empty textarea → `[]` |
| `test_result_matches_item_source_include` | include `["Lightning Bolt"]` matches `"Lightning Bolt (NM)"` |
| `test_result_matches_item_source_exclude` | exclude `["Foil"]` drops `"Lightning Bolt [Foil]"` |
| `test_empty_patterns_pass_all` | no patterns → matches any title |

## Definition of done

- [x] `result_matches_item_source()` in `tracking/matching.py`
- [x] `tracking/forms.py::ItemSourceForm` with regex validation and line↔JSON conversion
- [x] Summary behaviour is pattern-aware or documented as Option A (comment in `views.py`)
- [x] Patterns editable via a user route (own route, or confirmed provided by Step 7)
- [x] Ingest still stores all rows (unchanged)
- [x] New tests pass; `python manage.py test tracking` green

---

# Phase 2: Step 6

## Goal

Build the item detail / history page: a table of **all** stored `SearchResult` rows for an item plus a **per-source** Chart.js price-history line chart. Enable the "View History" link on the list page.

**plan.md bullet:** Phase 2 §2c — *"Item detail page: all-results table + per-source Chart.js history (variant rows visible; pattern-matched rows highlighted)."*

## Dependencies

- **Requires:** Phase 1 (`SearchResult.search_term`, `instock`, multiple rows/fetch; `WebUpdate.timestamp`).
- **Soft:** Step 5 (highlight pattern-matched rows — optional; render all rows without badges if Step 5 not merged). Steps 3/4 (multi-source charts look better, but CC-only works).
- **Independent of:** Steps 1, 2.

## Current state

- List template `tracking/templates/tracking/searchableitem_list.html` has a disabled "View History" placeholder and already loads Chart.js (reuse its CDN include).
- No item detail view/URL yet.

## Tasks

### Task 1 — View + URL

`tracking/urls.py`:
```python
path("item/<int:pk>/", views.SearchableItemDetailView.as_view(), name="item_detail"),
```

`tracking/views.py`:
```python
from django.views.generic import DetailView

class SearchableItemDetailView(DetailView):
    model = SearchableItem
    template_name = "tracking/searchableitem_detail.html"
    context_object_name = "item"
```

### Task 2 — Context data (`get_context_data`)

Provide:
1. `results` — `SearchResult.objects.filter(item=self.object).select_related("source", "update").order_by("-update__timestamp", "source__key", "price")`.
2. `chart_data_json` — JSON string, per source key: `{"cc": {"labels": [...], "prices": [...]}, ...}`. For each source, for each `WebUpdate` (chronological), the **lowest in-stock** price among that source's results for that update. Use `timezone.localtime()` for labels (Atlantic; match list page `%d/%m/%y`).
3. `item_sources` — `ItemSource.objects.filter(item=self.object).select_related("source")`.
4. `tags` — `self.object.tags.all()`.

If Step 5 is available, also compute a per-row `matches` flag via `matching.result_matches_item_source` (map result → its `ItemSource` by source) for highlighting; degrade gracefully if not.

### Task 3 — Template `searchableitem_detail.html`

Bootstrap, matching existing pages:
- **Header:** item text, priority, active badge, tags.
- **Nav:** back to list, edit item; "Manage sources" link to Step 7 route if present.
- **Charts:** one `<canvas id="chart-{source_key}">` per source with results; init from `chart_data_json`.
- **Table** (all rows): Date/time (`|date:"Y-m-d H:i"`, Atlantic), Source, Search term, Title, Price (`$`), In stock (yes/no badge), Category. Optional row CSS class when `matches` is false.

### Task 4 — Enable the list link

In `searchableitem_list.html`, replace the disabled placeholder with:
```html
<a href="{% url 'item_detail' item.pk %}" class="btn btn-info btn-sm">View History</a>
```

## Tests (append `class ItemDetailViewTests(TestCase)`)

| Test | Assert |
|------|--------|
| `test_item_detail_200` | create item + a `WebUpdate` + results → GET `item_detail` returns 200 |
| `test_item_detail_lists_all_results` | multiple `SearchResult` titles appear in the response |
| `test_item_detail_chart_context` | `chart_data_json` present and contains a series for a source with in-stock results |
| `test_list_page_history_link` | list page HTML contains `href` to `item_detail` |

## Definition of done

- [x] `SearchableItemDetailView` + `item/<pk>/` named `item_detail`
- [x] Template shows all `SearchResult` rows for the item
- [x] Per-source Chart.js line chart (lowest in-stock per update)
- [x] "View History" enabled on the list page
- [x] Tests pass; `python manage.py test tracking` green

---

# Phase 2: Step 7

## Goal

CRUD UI (no Django admin) for `Source` and for linking items to sources (`ItemSource`), including the `parser_key` selector, `request_headers`, and URL template.

**plan.md bullet:** Phase 2 §2c — *"Source + ItemSource management UI (`Source` form exposes `parser_key` dropdown from registry, `request_headers`, URL template; validate `{term}` for GET parsers)."*

## Dependencies

- **Requires:** Step 1 (`parser_key`, `request_headers`, widened `key`).
- **Coordinate with Step 5:** import `ItemSourceForm` from `tracking/forms.py` (Step 5 owns it). If Step 5 isn't merged, add a minimal `ItemSourceForm` and flag it for later reconciliation.
- **Independent of:** Steps 3, 4, 6 (the UI lists whatever `Source` rows exist).

## Current state

- Tag CRUD is the pattern to mirror: `TagListView/TagCreateView/TagUpdateView/TagDeleteView` in `views.py`; templates `tag_list.html`, `tag_form.html`, `tag_confirm_delete.html`; routes `view_tags/add_tag/edit_tag/delete_tag`.
- Parser registry: `tracking.parsers.sources` — keys are valid `parser_key`s.

## Tasks

### Task 1 — `SourceForm` (`tracking/forms.py`)

- `ModelForm` on `Source`, fields: `key`, `name`, `parser_key`, `base_search_url`, `request_headers`, `page_size`.
- `parser_key`: render as a **dropdown** populated from `parsers.sources.keys()` (e.g. `ChoiceField` with `choices=[(k, k) for k in sources]`) so users can't pick an unregistered parser.
- `clean_base_search_url`: require `"{term}"` (Phase 2 vendors are GET APIs). Help text: `Example: https://example.com/search?q={term}`.
- `key` on the **edit** form: read-only/disabled (it's the PK).
- Add Bootstrap widget classes.

### Task 2 — Source CRUD (`views.py`, `urls.py`, templates)

Mirror the Tag views/URLs. `Source.key` is the PK, so URL captures use `<str:pk>`:

| URL | Name | View |
|-----|------|------|
| `/sources/` | `view_sources` | `SourceListView` (annotate `item_count=Count("itemsource")`) |
| `/sources/add/` | `add_source` | `SourceCreateView` |
| `/sources/<str:pk>/edit/` | `edit_source` | `SourceUpdateView` |
| `/sources/<str:pk>/delete/` | `delete_source` | `SourceDeleteView` |

Templates: `source_list.html` (columns: key, parser_key + "registered ✓/✗", name, URL truncated, # items, actions), `source_form.html`, `source_confirm_delete.html` (warn that related `SearchResult`/`FetchJob` cascade-delete; show `ItemSource` count).

### Task 3 — ItemSource management (item-centric)

| URL | Name | Purpose |
|-----|------|---------|
| `/item/<int:pk>/sources/` | `item_sources` | list `ItemSource` for the item |
| `/item/<int:pk>/sources/add/` | `add_item_source` | create via `ItemSourceForm` |
| `/item_source/<int:pk>/edit/` | `edit_item_source` | edit suffix + patterns |
| `/item_source/<int:pk>/delete/` | `delete_item_source` | unlink |

Use the shared `ItemSourceForm`. On **add**, exclude already-linked sources from the `source` dropdown. Add `unique_together = [("item", "source")]` to `ItemSource.Meta` (+ migration depending on `0007`) if not already present.

### Task 4 — Navigation

- In `searchableitem_list.html` header, add `<a href="{% url 'view_sources' %}" ...>Manage Sources</a>`.
- Cross-link item detail (Step 6) → `item_sources` when present.

## Tests (append `class SourceManagementTests(TestCase)` + `class ItemSourceManagementTests(TestCase)`)

| Test | Assert |
|------|--------|
| `test_source_list_page` | 200; shows the CC source |
| `test_create_source_valid` | POST valid `{term}` URL + registered `parser_key` → `Source` created |
| `test_create_source_rejects_missing_term` | form error when URL has no `{term}` |
| `test_create_source_rejects_unknown_parser_key` | choice validation blocks unregistered key |
| `test_delete_source_confirm_shows_counts` | confirm page shows linked-item count |
| `test_add_item_source` | links item ↔ source |
| `test_duplicate_item_source_blocked` | `unique_together` prevents duplicate link |

## Definition of done

- [x] `SourceForm` (parser_key dropdown, `{term}` validation) + Source list/create/edit/delete pages
- [x] ItemSource list/add/edit/delete under the item, using shared `ItemSourceForm`
- [x] `unique_together` on `(item, source)` (+ migration if added)
- [x] "Manage Sources" nav link from the item list
- [x] Admin not required for normal source/item-source management
- [x] Tests pass; `python manage.py test tracking` green

---

# Phase 2: Completion checklist (all steps)

- [ ] Step 1 — `Source` JSON-API config fields + migration
- [ ] Step 2 — `JSONSearchParser` + uniform `parse_response` + orchestrator/`Fetcher` refactor
- [ ] Step 3 — `ShopifyParser` (Shopify vendor) + `Source` row
- [ ] Step 4 — `StorepassParser` (Storepass vendor) + `Source` row
- [ ] Step 5 — `ItemSource` pattern form + matching in summaries
- [ ] Step 6 — Item detail page (all results + per-source charts)
- [ ] Step 7 — Source + ItemSource management UI
- [ ] `python manage.py test tracking` — all pass
- [ ] Update [plan.md](plan.md) Phase 2 checkboxes (outside agent scope unless instructed)
