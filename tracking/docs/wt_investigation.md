# Wizard's Tower — Search Investigation

**Date:** 2026-07-07  
**Test query:** `Lightning Bolt`  
**Investigator:** vendor investigation (automated fetch + DOM/API review)

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://store.wizardtower.com/search?q={term}
```

Example: `https://store.wizardtower.com/search?q=Lightning+Bolt`

- Query parameter: `q`
- `{term}` via `Source.build_search_url()` (`urllib.parse.quote_plus`) → `Lightning+Bolt` (verified).

### Machine-facing search API (actual product data)

Wizard's Tower uses a custom **wt-filters** Shopify app with an external API host.

```
POST https://app-filters.wizardtower.com/api/search
```

`base_search_url` for a future parser should point to this API endpoint. Because the scrape orchestrator currently uses `Fetcher.get()` (HTTP GET), implementing this vendor will likely require either:

1. A parser-specific POST fetch in `scrape._run_parser_search`, or
2. Encoding search parameters in a custom fetch method on the parser.

**POST body template:**

```json
{
  "context": {
    "mode": "buy",
    "page": 1,
    "per_page": 24,
    "sort": "manual"
  },
  "filters": [],
  "q": "{term}",
  "include_facets": false,
  "preview": false
}
```

Replace `{term}` with the actual search string at request time (not URL-encoded inside JSON).

**Related endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/search` | POST | Full search results |
| `/api/suggest?q={term}` | GET | Typeahead suggestions |
| `/api/filter` | POST | Collection filtering |

`WT_FILTER_CONFIG.apiBase` is set to `https://app-filters.wizardtower.com` in the storefront.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL returns product cards with prices | **No** — no static `$` prices in HTML (0 in stripped source) |
| View source contains static product prices | **No** |
| Products require browser JS | **Yes** — `wt-filter.js` renders results client-side |
| JSON API returns structured products without JS | **Yes** — `POST /api/search` returns JSON |

**Decision:** `JS_RENDERED_WITH_JSON_API`

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **No** |
| `BEAUTIFULSOUP` | **No** |
| `PLAYWRIGHT_DEFERRED` | **No** — wt-filters API works with `requests` POST |

**Recommendation:** Implement a **JSON API parser** using POST to `/api/search`. Include `Origin` and `Referer` headers from the storefront.

---

## 3. Sample request headers

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Content-Type: application/json
Accept: application/json
Origin: https://store.wizardtower.com
Referer: https://store.wizardtower.com/search?q=Lightning+Bolt
```

No cookies or auth required (verified 2026-07-07). Bare POST without `context` object returns `400 INVALID_REQUEST`.

---

## 4. Data selector map

### Product container

| JSON path | Notes |
|-----------|-------|
| `data.results[]` | One result per product listing |
| `data.pagination` | Page metadata |
| `data.facets` | Filter facets (when `include_facets: true`) |

### Title

| JSON path | Notes |
|-----------|-------|
| `data.results[].title` | Full title, e.g. `Lightning Bolt (042) (STA) - Foil` |
| `data.results[].display_name` | Shorter name, e.g. `Lightning Bolt` |
| `data.results[].card_name` | Card name for matching |

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `data.results[].price` | Float, lowest/display price |
| `data.results[].compare_at_price` | Optional strike-through price |
| `data.results[].variant_count` | Number of variants (detail fetched separately) |

### In stock

| JSON path | Notes |
|-----------|-------|
| `data.results[].in_stock` | Boolean |
| `data.results[].total_inventory` | Aggregate quantity |

**Recommended instock rule:** `in_stock == true` (per product row; variants collapsed in search results).

### Category / set metadata

| JSON path | Notes |
|-----------|-------|
| `data.results[].category` | e.g. `Magic the Gathering Singles` |
| `data.results[].subcategory` | e.g. `Strixhaven - Mystical Archive` |
| `data.results[].tags[]` | Finish/set tags, e.g. `{field: "mtg_mtg_finish", value: "Foil"}` |
| `data.results[].collector_number` | e.g. `0042` |

**item-category-relevance-filter:** wt's own field literally named `category` is actually the *broad* game/product-line signal (e.g. `Magic the Gathering Singles`), not the narrow set/printing one — it feeds this app's `expected_product_line` check and the `product_line` column. The narrow set/printing signal — `subcategory` (preferred) or `category` as fallback — is what this app's own `category` field/column and `expected_category` check use, unchanged from before this capability. See `design.md` Decision 6/7 under `openspec/changes/item-category-relevance-filter/` for the full rationale.

### HTML DOM (reference only — JS-rendered)

| Field | CSS / attribute |
|-------|-----------------|
| Search input | `[data-wt-search-input]` |
| API config | `window.WT_FILTER_CONFIG.apiBase` |
| Product card | `.wt-product-card` (synthetic; rendered by wt-filter.js) |

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `Lightning Bolt` |
| API `pagination.total_results` | **627** |
| API `pagination.total_pages` | 27 (at `per_page=24`) |
| Results per page | 24 |

Pagination: increment `context.page` in the POST body.

---

## 6. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **POST-only API** — incompatible with GET-only scrape path | High | Extend `scrape._run_parser_search` or parser fetch hook |
| **Third-party host** — `app-filters.wizardtower.com` external to Shopify | High | Pin fixtures; monitor API availability |
| **CORS / Origin checks** — API may require storefront Origin | Medium | Send `Origin` + `Referer` headers |
| **Variant detail** — search results collapse variants (`variant_count` > 1) | Medium | May need `/api/stock` POST for per-condition prices |
| **Generic/broad search matching** — vendor search is not phrase-aware; e.g. `"The Queen of Dale"` returns 30,277 total results including non-cards (`"Deck Box - The Hobbit - The Queen Of Dale"`) and unrelated cards sharing only some words (`"Chip 'N' Dale, Recovery Rangers"`); `"Fire Dragon"` (an MTG card) returns listings of `"Dragon Fire"`, a different card from a different game (Disney Lorcana) | Medium | `JSONSearchParser` now rejects any result row whose title does not contain the search term as a contiguous phrase (see the `search-term-relevance` capability / `WtFiltersParser`) — a baseline, always-on filter, independent of the optional per-item `ItemSource` patterns below |
| **Foil ambiguity** — titles include finish in name/tags | Low | `ItemSource` exclude patterns |

---

## 7. Recommended parser approach

**Base class:** Custom JSON parser with **POST** fetch — **not** `SearchParser` HTML traversal.

**Suggested implementation sketch:**

```python
class WizardsTowerParser:
    API_URL = "https://app-filters.wizardtower.com/api/search"

    def fetch_search(self, term: str, page: int = 1) -> dict:
        body = {
            "context": {"mode": "buy", "page": page, "per_page": 24, "sort": "manual"},
            "filters": [],
            "q": term,
            "include_facets": False,
            "preview": False,
        }
        # POST with Origin/Referer headers
        ...

    def parse_response(self, data: dict) -> None:
        for row in data.get("data", {}).get("results", []):
            self.results.append({
                "title": row.get("title", ""),
                "price": float(row["price"]),
                "instock": bool(row.get("in_stock")),
                "category": row.get("subcategory") or row.get("category", ""),
            })
```

**`Source` row suggestion:**

```python
WT_DEFAULT_SEARCH_URL = "https://app-filters.wizardtower.com/api/search"
```

The search term is passed in the POST body (`q`), not the URL `{term}` placeholder. Options:

- Store API URL in `base_search_url` without `{term}` and pass term via parser, or
- Use a convention like `https://app-filters.wizardtower.com/api/search#term={term}` parsed by the parser (non-standard).

**Fixtures:**

- Primary: `tracking/fixtures/html/wt/search_results_sample.json`
- Reference: `tracking/fixtures/html/wt/search_results_sample.html`

---

## Appendix: Platform details

- **Platform:** Shopify (custom theme with wt-filters app)
- **Search app:** `wt-filters` — JS at `cdn.shopify.com/extensions/.../wt-filters-7/assets/wt-filter.js`
- **API host:** `https://app-filters.wizardtower.com`
- **Suggest API:** `GET /api/suggest?q=...` (returns label suggestions, not full product data)
- **Currency:** CAD (prices returned as floats, e.g. `10.00`)
