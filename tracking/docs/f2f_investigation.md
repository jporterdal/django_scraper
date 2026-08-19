# Face to Face Games — Search Investigation

**Date:** 2026-07-07  
**Test query:** `Lightning Bolt`  
**Investigator:** Phase 2 Step 1 (automated fetch + manual DOM review)

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://facetofacegames.com/search?q={term}
```

Example: `https://facetofacegames.com/search?q=Lightning+Bolt`

- Query parameter: `q`
- `{term}` replacement via `Source.build_search_url()` (`urllib.parse.quote_plus`) produces `Lightning+Bolt`, which the site accepts.
- `+` vs `%20`: both work for the `q` parameter and for the API path segment (verified).

### Machine-facing search API (actual product data)

```
https://facetofacegames.com/apps/prod-indexer/search/pageSize/{page_size}/page/{page}/keyword/{term}
```

Recommended defaults for scraping:

```
https://facetofacegames.com/apps/prod-indexer/search/withFacets/false/pageSize/100/page/1/minimum_price/0.01/keyword/{term}
```

Example:

```
https://facetofacegames.com/apps/prod-indexer/search/withFacets/false/pageSize/100/page/1/minimum_price/0.01/keyword/Enduring+Innocence
```

**Note for step 2:** `base_search_url` should point to the **API URL** (not the HTML search page), because product listings are not present in static HTML. The HTML page URL is useful only for manual browsing.

The site also embeds `searchQuery()` logic that double-encodes keywords (`encodeURIComponent(encodeURIComponent(q))`). Single `quote_plus` encoding works in practice; double encoding also works.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL returns product cards with prices | **Partial** — page shell only; prices use Alpine.js `x-text` bindings |
| View source contains static product prices | **No** — prices bound via `x-text="getPrice(v.price)"` |
| Products require browser JS | **Yes** — Alpine.js renders `displayedHits` from API |
| JSON API returns structured products without JS | **Yes** — `/apps/prod-indexer/search/...` returns JSON |

**Decision:** `JS_RENDERED_WITH_JSON_API`

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **No** — product rows are not in static HTML |
| `BEAUTIFULSOUP` | **No** — same reason |
| `PLAYWRIGHT_DEFERRED` | **No** — JSON API is fetchable with `requests` |

**Step 2 recommendation:** Implement a **JSON API parser** (`FaceToFaceParser`) that:

1. Fetches the prod-indexer URL built from `Source.base_search_url`
2. Parses `response.json()["hits"]["hits"]`
3. Emits one result row per **variant** (NM/PL/HP conditions), matching how the storefront displays cards

`SearchParser.feed()` expects HTML; the F2F parser should override parsing (e.g. custom `parse_json()` called from a thin wrapper, or override `feed()` to detect JSON content-type). Step 2 may need a small hook in `scrape._run_parser_search` if the parser should not call `feed(html)`.

---

## 3. Sample request headers

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: application/json
Accept-Language: en-CA,en;q=0.9
```

For the HTML page (navigation only):

```http
Accept: text/html,application/xhtml+xml
```

No cookies or auth required for search (verified 2026-07-07).

---

## 4. Data selector map

Because results come from JSON (Elasticsearch-style hits), use JSON paths rather than CSS selectors.

### Product container

| JSON path | Notes |
|-----------|-------|
| `hits.hits[]` | One hit per Shopify product (may have multiple variants) |
| `hits.hits[]._source` | Product document |

### Title

| JSON path | Notes |
|-----------|-------|
| `_source.title` | Full listing title, e.g. `Lightning Bolt [146] [Magic 2010] [Foil]` |
| `_source.General_Card_Name` | Shorter card name, e.g. `Lightning Bolt` — prefer for display matching |
| `_source.MTG_Set_Name` | Set name metafield |
| `_source.General_Game_Type` (or `_source["Game Type"]`) | Array, e.g. `["Magic: The Gathering"]` — broad game/product-line signal |

**item-category-relevance-filter:** `General_Game_Type`/`Game Type` (broad, e.g. `Magic: The Gathering`) feeds this app's `expected_product_line` check and the `product_line` column. `MTG_Set_Name`/`Set` (narrow, set-level) is what this app's own `category` field/column and `expected_category` check use, unchanged from before this capability. See `design.md` Decision 6/7 under `openspec/changes/item-category-relevance-filter/` for the full rationale.

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `_source.variants[].price` | Float, already CAD for `en` locale |
| `_source.variants[].compareAtPrice` | String, optional strike-through price |

Storefront applies `window.Shopify.currency.rate`; for CAD locale this is `1.0`.

### In stock

| JSON path | Notes |
|-----------|-------|
| `_source.variants[].inventoryQuantity` | Integer; `> 0` = in stock |
| Product-level aggregate | Sum of variant quantities (used in Alpine `renderProductCard`) |

**Recommended instock rule:** `inventoryQuantity > 0` per variant row.

### Variant / condition

| JSON path | Notes |
|-----------|-------|
| `_source.variants[].selectedOptions[0].value` | Condition: `NM`, `PL`, `HP` |
| `_source.variants[].sku` | e.g. `M-M10-Lightning_-146-NM-F` |

### HTML DOM (reference only — JS-rendered)

If ever parsing HTML, Alpine templates use:

| Field | CSS / attribute |
|-------|-----------------|
| Product card | `.bb-card-wrapper` |
| Title | `.bb-card-title`, `.f2f-fv-title-t` |
| Price | `.price-item.price-item--regular` |
| Inventory | `.f2f-fv-buy`, `inventory_message` in Alpine |

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `Lightning Bolt` |
| API total (`hits.total.value`) | **100** (relation `eq`) |
| Products per page (pageSize=24) | 24 |
| Variant rows on first page | **52** (multiple conditions per product) |

Pagination: increment `/page/{n}` until `hits.hits` is empty. Default storefront `pageSize` is 24; use `pageSize=100` for fewer requests if the API allows.

---

## 6. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Unofficial API** — `/apps/prod-indexer/search` is a Shopify app endpoint, not documented | High | Pin fixture tests; monitor for 404/shape changes; log `FetchJob` errors |
| **Rate limiting / bot detection** | Medium | Use existing `Fetcher` rate limits; polite User-Agent |
| **Pagination cap** — total may exceed 100 for broad queries | Medium | Document; fetch multiple pages in step 2 if needed |
| **Variant explosion** — one card → many NM/PL/HP rows | Low | Expected; use `ItemSource` include/exclude patterns (step 3) |
| **Foil / set ambiguity** — titles include `[Foil]`, set names in brackets | Low | Pattern fields: exclude `Foil`, include specific set |
| **Locale paths** — `/locale/fr` prefix for French | Low | Use default English URLs |
| **Currency** — non-CAD locales apply `Shopify.currency.rate` | Low | Hard-code CAD URLs (`facetofacegames.com` default) |

---

## 7. Recommended parser approach for step 2

**Base class:** Custom JSON parser — **not** `SearchParser` HTML traversal.

**Suggested implementation sketch:**

```python
class FaceToFaceParser:
    """Parse F2F prod-indexer JSON search responses."""

    def __init__(self, term=""):
        self.term = term
        self.results = []

    def parse_response(self, response_text: str) -> None:
        data = json.loads(response_text)
        for hit in data.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            for variant in src.get("variants", []):
                self.results.append({
                    "title": src.get("title", ""),
                    "price": float(variant["price"]),
                    "instock": variant.get("inventoryQuantity", 0) > 0,
                    "category": src.get("MTG_Set_Name", ""),
                })
```

**`Source` row (step 2):**

```python
F2F_DEFAULT_SEARCH_URL = (
    "https://facetofacegames.com/apps/prod-indexer/search"
    "/pageSize/100/page/1/keyword/{term}"
)
```

**Fixtures for tests:**

- Primary: `tracking/fixtures/html/f2f/search_results_sample.json`
- Reference HTML: `tracking/fixtures/html/f2f/search_results_sample.html` (synthetic fragment)

**Scrape integration:** Ensure `scrape._run_parser_search` can pass JSON body to the parser. If `FaceToFaceParser` subclasses `SearchParser`, override `feed()` to parse JSON instead of calling `HTMLParser.feed`.

---

## Appendix: Platform details

- **Platform:** Shopify (Dawn-derived theme)
- **Search UI:** Alpine.js (`x-data="rootProducts()"`, `renderProductCard(item._source)`)
- **Search backend:** Custom Shopify app `prod-indexer` (Elasticsearch-style `hits.hits[]._source`)
- **OG meta:** `Search: 280 results found for "Lightning Bolt"` on HTML page (may differ from API total due to filtering)
