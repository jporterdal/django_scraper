# Citadel Music — Search Investigation

**Date:** 2026-08-17
**Test query:** `pedal`
**Investigator:** vendor investigation (automated fetch + manual DOM/API review)

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://citadelmusichfx.com/search?q={term}
```

Example: `https://citadelmusichfx.com/search?q=pedal`

- Query parameter: `q`
- `{term}` via `Source.build_search_url()` (`urllib.parse.quote_plus`) produces `pedal` (single-word query; no encoding edge case exercised).
- Unlike the other three vendors investigated so far (f2f, hfx, wt), **this page is genuinely server-rendered.** It's Shopify's stock Dawn theme `/search` template, rendered in Liquid on Shopify's servers — not a JS SPA overlay. `curl`ing it directly returns full product cards (title, price, image) with no browser required. Page title: `Search: 23 results found for "pedal"`; `.product-count__text` also reports `23 results`.

### Machine-facing search API (actual product data)

Citadel Music is a stock Shopify store with **no bespoke third-party search app** (no Storepass, no Algolia/Klevu-style widget, no `wt-filters`-style external host detected). The standard, Shopify-native, publicly documented, unauthenticated way to search this storefront by keyword is Shopify's own **Predictive Search API**:

```
GET https://citadelmusichfx.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10
```

Example: `https://citadelmusichfx.com/search/suggest.json?q=pedal&resources[type]=product&resources[limit]=10`

This was tried first per the investigation brief and it worked immediately: HTTP 200, `Content-Type: application/json`, ten real products back for `q=pedal` (Outlaw Effects pedals, an MXR Booster Mini, etc.) with title/price/availability/type/vendor fields populated. No fallback to `/products.json?title={term}` was needed or attempted — `/products.json` (confirmed separately to return valid Shopify product JSON, prior to this investigation) is a full-catalog listing endpoint with no genuine server-side keyword-filtering guarantee, whereas `/search/suggest.json` is Shopify's actual documented search endpoint, so it's the correct choice and no further endpoint comparison was performed.

**Important trap to flag:** the response headers include `search-engine: elasticsearch`. This does **not** mean the response body is Elasticsearch-`hits.hits[]`-shaped like f2f's third-party search app. It's just Shopify revealing that its own backend infrastructure happens to use Elasticsearch internally — the JSON shape returned to the client is Shopify's own native predictive-search shape (`resources.results.products[]`), completely unrelated to the `_source`-wrapped ES-hit documents `ShopifyParser` expects. See Section 4.

**Note for step 2:** `base_search_url` should point to the **API URL**. The HTML page also happens to carry full product data (see above), so unlike f2f/hfx/wt, an `HTML_PARSER`-style approach is *also* viable here — but the JSON API is still the better choice: it returns clean structured fields (title, price, availability, category) with no DOM traversal, matching how this app's other JSON-backed vendors are built.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL returns product cards with prices | **Yes** — 23 `.card--product` elements, each with a static `.price-item.price-item--regular` span (e.g. `$79.00 CAD`) |
| View source contains static product prices | **Yes** — Liquid-rendered, not JS-bound |
| Products require browser JS | **No** — neither the HTML search page nor the JSON API needs a browser |
| JSON API returns structured products without JS | **Yes** — `/search/suggest.json` returns JSON directly via `requests.get` |

**Decision:** `JSON_API_DIRECT` (no JS rendering involved at any point — this vendor differs from f2f/hfx/wt, all three of which required a `JS_RENDERED_WITH_JSON_API` classification because their HTML search pages were empty shells).

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **Yes, viable but not recommended** — static HTML has everything needed (`CCSearchParser`-style selectors would work: `.card-information__text a` for title, `.price-item--regular` for price); the JSON API is cleaner and preferred |
| `BEAUTIFULSOUP` | N/A — not needed if the JSON route is taken |
| `PLAYWRIGHT_DEFERRED` | **No** — neither route needs a browser |

**Step 2 recommendation:** the JSON API (`/search/suggest.json`) does **not** match any parser currently registered in `tracking/parsers.py` (`CCSearchParser`, `ShopifyParser`, `StorepassParser`, `WtFiltersParser`) — see Section 4. Writing a new parser is out of scope for this investigation; that gap is documented here and in the fixture README rather than filled. If a parser is written later, it should implement `JSONSearchParser.parse_data()` reading `data["resources"]["results"]["products"]`, using the product-level `price`/`available` fields (not `variants[]`, which predictive search always returns empty — see Section 4).

---

## 3. Sample request headers

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: application/json
Accept-Language: en-CA,en;q=0.9
```

No cookies or auth required for the request itself (verified 2026-08-17) — the response does set several `_shopify_*` tracking cookies (`_shopify_y`, `_shopify_s`, `_shopify_essential`, `_shopify_analytics`, `_shopify_marketing`), but none are required on the request side to get a valid search response.

For the HTML page (navigation/manual review only):

```http
Accept: text/html,application/xhtml+xml
```

---

## 4. Data selector map

Results come from JSON, not HTML — use JSON paths.

### Product container

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[]` | One entry per matching product. Capped at `resources[limit]` (max 10 — see Section 6/Pagination) |

### Title

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].title` | e.g. `Outlaw Effects 24K Reverb` |
| `resources.results.products[].vendor` | Observed to be `Fantasie Musical Instruments` for every sampled product — this looks like a POS/import-system artifact rather than a real per-product manufacturer field on this store, so it is **not** reliable as a brand/manufacturer signal despite the field name |

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].price` | **String**, e.g. `"69.00"` — must be cast to float, unlike `ShopifyParser`'s expectation of a numeric `variant["price"]` |
| `resources.results.products[].price_min` / `price_max` | Same value as `price` in all sampled rows (no price-range products observed in this sample) |
| `resources.results.products[].compare_at_price_min` / `compare_at_price_max` | `"0.00"` in all sampled rows — i.e. no active compare-at/sale pricing on this catalog snapshot |

### In stock

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].available` | Boolean, product-level. **This is the field to use** — see note below |

**Recommended instock rule:** `available == true` (product-level boolean). This is a deliberate deviation from `ShopifyParser`'s `variant["inventoryQuantity"] > 0` pattern — see next section for why.

### Category

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].type` | Hierarchical string, e.g. `Effects and Pedals > Reverb`, `Effects and Pedals > Overdrive and Boost`. Top-level products (no subtype) return a bare string, e.g. `Effects and Pedals` |

### Variants — **empty in this response, unlike f2f's shape**

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].variants[]` | **Always `[]` (empty array) in every sampled product** — Shopify's predictive search endpoint does not populate per-variant data by default. This is the concrete point of divergence from `ShopifyParser.parse_data()`, which reads `hits.hits[]._source.variants[].price` / `.inventoryQuantity` / `.selectedOptions[]` — none of that exists here because `variants` is empty, so `ShopifyParser` run against this response would silently produce **zero results**, not an error. |

### Why `ShopifyParser` does not match

`ShopifyParser.parse_data()` (`tracking/parsers.py`) expects:

```python
data.get("hits", {}).get("hits", [])
# each hit: hit["_source"]["title"], hit["_source"]["variants"][]["price"],
#            hit["_source"]["variants"][]["inventoryQuantity"], ...
```

The Citadel Music response has **no `hits` key at all**. Its top-level shape is:

```python
data["resources"]["results"]["products"]
# each product: product["title"], product["price"] (string),
#                product["available"] (bool), product["type"], product["variants"] == []
```

These are structurally incompatible — `ShopifyParser` against this payload would call `.get("hits", {})` → `{}` → `.get("hits", [])` → `[]` and iterate zero times, silently returning no results rather than erroring. **No registered parser in `tracking/parsers.py` matches this response shape.** A new parser (tentatively `ShopifyPredictiveSearchParser` or similar) would be needed; not written here per the investigation's scope.

### HTML DOM (reference only — static, not currently recommended)

If an `HTML_PARSER` route were chosen instead (Dawn theme, `/search?q={term}`):

| Field | CSS / attribute |
|-------|-----------------|
| Product card | `.card-wrapper` (inside `li.grid__item`) |
| Title | `.card-information__text a.full-unstyled-link` |
| Price | `.price .price-item.price-item--regular` (format: `$79.00 CAD`) |
| Image | `img` inside `.media` |
| Result count | `.product-count__text` (e.g. `23 results`) |

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `pedal` |
| Predictive Search API (`resources[limit]=10`) | **10** (hard-capped, see Pagination) |
| Predictive Search API (`resources[limit]=50`, tried explicitly) | still **10** — confirms the documented ceiling is enforced server-side, not just a default |
| HTML `/search?q=pedal` page (`.product-count__text`) | **23 results** total in the full catalog for this query |
| Same query, `guitar` (broader term, spot-checked) | 10 returned (limit-capped again; true total not checked) |

---

## 6. Pagination

**The Predictive Search API does not support pagination at all.** Per Shopify's own documentation (`shopify.dev/docs/api/ajax/reference/predictive-search`), `resources[limit]` accepts `1`–`10` only (default `10`), and "the API returns no more than 10 predictive suggestions per request type" — there is no `page`/`offset`/cursor parameter of any kind. This was confirmed empirically above: requesting `resources[limit]=50` still returned exactly 10 products for a query with 23 true matches. This is a materially different limitation from f2f (`ShopifyParser.next_page_url` increments `/page/{n}/` until empty), hfx (`StorepassParser` reads `current_page`/`pages`), and wt (`WtFiltersParser`, POST-paginated) — those all have a genuine "next page" concept in their APIs; Citadel's predictive search endpoint simply does not, by design (it's built for typeahead/autocomplete UI, not exhaustive search results listing).

Practical implication: a query returning more than 10 true matches (like `pedal`, with 23) will only ever surface the top 10 via this endpoint, with no way to page to the rest through this API. If exhaustive results matter for this vendor in the future, the alternative would be Shopify's native paginated `/search?q={term}&page={n}` HTML page (see Section 2 — confirmed server-rendered and scrapable) or Shopify's Storefront GraphQL API's `search` query (requires a Storefront API access token — an authenticated, higher-effort path out of scope here).

---

## 7. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Result cap** — predictive search hard-caps at 10 results per query, no pagination | High | Document clearly (done above); if exhaustive coverage is needed, plan for the paginated HTML page or GraphQL Storefront API instead |
| **Empty `variants[]`** — any future parser modeled on `ShopifyParser`'s variant-reading pattern will silently return zero rows | High | Document explicitly (done above); a new parser must read product-level `price`/`available`, not `variants[]` |
| **String-typed price** — `price` field is a string (`"69.00"`), not numeric | Low | Cast with `float()` in any new parser, same as most JSON vendors already require |
| **Undocumented exact rate limit** — Shopify confirms 429 + `Retry-After` on throttle but does not publish the numeric threshold | Medium | Rely on this app's fixed-delay pacing; watch for 429s in production `FetchJob` logs |
| **`vendor` field unreliable as brand signal** — always `Fantasie Musical Instruments` in sampled data | Low | Do not use `vendor` for category/brand filtering; use `type` instead |
| **No parser currently registered for this shape** | High (blocking) | New parser required before this vendor can be scraped; explicitly out of scope for this investigation |

---

## 8. Rate-limit signals

### Empirical

Checked 2026-08-17 with two live GETs to `/search/suggest.json` (`q=pedal`, `resources[limit]=10` and `resources[limit]=50`) plus one to `q=guitar`. Full response header set inspected for the first request:

```
date, content-type, x-download-options, x-xss-protection, set-cookie (x5, all _shopify_* tracking cookies),
x-content-type-options, search-engine, x-dc, report-to, nel, shopify-complexity-score,
shopify-complexity-score-v2, x-frame-options, content-security-policy, strict-transport-security,
vary (x2), alt-svc, content-language, powered-by, server-timing, x-permitted-cross-domain-policies,
cf-cache-status, x-request-id, server, etag, cf-ray
```

None of `RateLimit-*` / `X-RateLimit-*` (the two header-based profiles this app checks for) are present. There is also no `extensions.cost` block in the body — this is a plain REST-shaped JSON response (`resources.results.products`), not GraphQL, so the `graphql_cost` profile doesn't apply either. `shopify-complexity-score` / `shopify-complexity-score-v2` headers **are** present (`2070` / `207` on this request) — these appear to be Shopify's internal Liquid/backend rendering-cost telemetry (unrelated to the three rate-limit profiles this app's `rate_limit_profile` field models), not a documented, actionable client-facing rate-limit signal; treated as informational only.

This is three healthy-path samples, not sustained-load testing — consistent with the other three vendors' investigations, this can't rule out throttling that only appears under volume.

### Published platform documentation

Checked 2026-08-17 via `shopify.dev`. Unlike f2f (unidentified third-party app) and hfx (Storepass, no public API docs found), this endpoint **is** Shopify's own documented, first-party API, so platform documentation is directly applicable:

- **Predictive Search API reference** (`shopify.dev/docs/api/ajax/reference/predictive-search`) confirms: exceeding the request throttle limit returns **HTTP 429** with a `Retry-After` header (value in seconds), but the exact numeric threshold (requests/second or /minute) is **not published**.
- The same reference documents the **10-result-per-type ceiling** used in Section 6 above (`resources[limit]` range `1`–`10`, default `10`).
- Shopify's general API usage-limits page (`shopify.dev/docs/api/usage/limits`) is scoped to authenticated Admin/GraphQL/Storefront API usage (apps calling Shopify with API credentials) — the same scoping caveat noted in the f2f investigation applies here too; that page's specific numeric buckets (e.g. GraphQL's leaky-bucket points/sec) are not established as applying to the unauthenticated Predictive Search endpoint specifically.

**Net effect:** a real, vendor-confirmed rate limit exists on this endpoint (429 + `Retry-After`), but its numeric value is undocumented. Recommend leaving `rate_limit_profile` blank (none of `ietf`/`x-ratelimit`/`graphql_cost` match what's actually returned) and relying on this app's fixed-delay pacing, same as the other three vendors — but note that if 429s do occur in production, the vendor's `Retry-After` header (once seen on an actual throttled response) would be directly actionable, unlike f2f/hfx where no such mechanism was even confirmed to exist.

Sources consulted: [Predictive Search API reference](https://shopify.dev/docs/api/ajax/reference/predictive-search), [Shopify API usage limits](https://shopify.dev/docs/api/usage/limits).

---

## 9. Recommended parser approach for step 2 (not implemented here)

**Base class:** `JSONSearchParser` (same base as `ShopifyParser`/`StorepassParser`/`WtFiltersParser`), **not** a new `ShopifyParser` variant reusing its variant-loop logic — the shapes are incompatible (Section 4).

**Parsing sketch** (illustrative only — not implemented per scope):

```python
class ShopifyPredictiveSearchParser(JSONSearchParser):
    """Shopify native Predictive Search API (/search/suggest.json)."""

    def parse_data(self, data):
        products = data.get("resources", {}).get("results", {}).get("products", [])
        for product in products:
            self.add_result(
                title=product.get("title", ""),
                price=product.get("price", 0),
                instock=product.get("available", False),
                category=product.get("type", ""),
            )

    def next_page_url(self, response, current_url, page_number):
        return None  # no pagination support — see Section 6
```

**Suggested `base_search_url`:**

```
https://citadelmusichfx.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10
```

**Fixture for tests:**

- `tracking/fixtures/html/citadel/search_results_sample.json` (this investigation's capture, 10 products, `q=pedal`)

---

## Appendix: Platform details

- **Platform:** Shopify (Dawn theme, stock — no custom third-party search app detected)
- **Search backend:** Shopify's own first-party Predictive Search API (`/search/suggest.json`) — genuinely native, unlike f2f's unidentified third-party app or hfx's Storepass SaaS
- **Currency:** CAD (`content-language: en-CA` header; prices formatted `$XX.XX CAD` on the HTML page)
- **CDN/edge:** Cloudflare (`server: cloudflare`, `cf-ray`, `cf-cache-status: DYNAMIC`)
- **HTML search page:** server-rendered (Liquid), no JS required — atypical among the four vendors investigated so far
