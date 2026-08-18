# Just Us! Coffee — Search Investigation

**Date:** 2026-08-17
**Test query:** `Chasing Tides`
**Investigator:** vendor investigation (automated fetch + manual DOM/API review)

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://justuscoffee.com/search?q={term}
```

Example: `https://justuscoffee.com/search?q=Chasing+Tides`

- Query parameter: `q`
- `{term}` via `Source.build_search_url()` (`urllib.parse.quote_plus`) → `Chasing+Tides` (verified — page loads and echoes `canonical_url` with the encoded query).

### Machine-facing search API (actual product data)

Just Us! Coffee is a stock Shopify storefront (no bespoke third-party search app was found — see Appendix). The **Shopify-native Predictive Search API** was tried first, per the standard unauthenticated storefront convention, and it works:

```
GET https://justuscoffee.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10
```

Example (URL-encoded, as `requests`/`Source.build_search_url` would send it):

```
https://justuscoffee.com/search/suggest.json?q=Chasing+Tides&resources%5Btype%5D=product&resources%5Blimit%5D=10
```

Verified live 2026-08-17: `200 OK` in ~0.40s, returned 3 real products (`Chasing Tides Medium/Light/Dark Roast`) matching the storefront catalog. This is Shopify's own documented `/{locale}/search/suggest.json` endpoint ([shopify.dev/docs/api/ajax/reference/predictive-search](https://shopify.dev/docs/api/ajax/reference/predictive-search)) — not a third-party app, unlike `f2f`'s `prod-indexer` or `hfx`'s Storepass integration.

**`/products.json` was not needed as a fallback** — the predictive search endpoint returned real, query-filtered results on the first try, satisfying the investigation's stated preference ("if it returns real product results, that's very likely your endpoint").

**Important caveat — response shape is Shopify's native shape, not the `f2f` Elasticsearch shape.** The response is `{"resources": {"results": {"products": [...]}}}`, a flat list of product objects. It is **not** `hits.hits[]._source...`. See §4 and the parser-match discussion below.

**Important caveat — `variants` is always empty.** Every product object in the predictive-search response carries `"variants": []`. Shopify's predictive search intentionally omits full variant data (it's designed for typeahead dropdowns, not full catalog data); price/availability are only exposed at the **product** level (`price`, `price_min`, `price_max`, `available`), not per-variant. This matters for the data selector map below.

**Pagination is capped, not paged.** `resources[limit]` accepts 1–10 (default 10); the API returns "no more than 10 predictive suggestions per request type" and has no page/offset/cursor parameter at all (confirmed via [shopify.dev's Predictive Search API reference](https://shopify.dev/docs/api/ajax/reference/predictive-search)). A query matching more than 10 products cannot be paged through this endpoint — the 11th+ results are simply unreachable via `/search/suggest.json`, unlike `f2f`'s `/page/N/` or `hfx`'s Storepass `current_page`/`pages`.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL returns product cards with prices | **No** — the results container is empty in server HTML |
| View source contains static product prices | **No** — all visible `$` amounts in the raw HTML are `$0.00` placeholders |
| Products require browser JS | **Yes** — see below |
| JSON API returns structured products without JS | **Yes** — `/search/suggest.json` returns JSON directly via `requests` |

Detail: `GET https://justuscoffee.com/search?q=Chasing+Tides` returns `200 OK` with a server-rendered page shell. The result **count** is server-rendered (`<meta property="og:title" content="Search: 3 results found for &quot;Chasing Tides&quot;">`, and body text `3 results found for "Chasing Tides"` / `Showing 1 - 3 of 3 results`), but the actual product grid container is emitted empty:

```html
<div id="main-collection-product-grid" data-id="template--18846793859234__main"
     class="collection grid grid--layout grid-4 grid-portable-3 grid-lap-2 grid-palm-1"></div>
```

No `/products/...` product links and no non-zero prices appear anywhere in the raw HTML outside of `<script>`-embedded analytics/tracking JSON (e.g. a `search_submitted` pixel event payload). The grid is populated client-side after page load — almost certainly via the same predictive-search machinery (there is a separate, always-empty `<div class="search-results-container" data-js-search-results></div>` used by the header's live-typing dropdown, confirming the theme leans on `/search/suggest.json`-style JS hydration rather than server-rendered Liquid loops for search).

**Decision:** `JS_RENDERED_WITH_JSON_API`

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **No** — no product data in static HTML |
| `BEAUTIFULSOUP` | **No** — same reason |
| `PLAYWRIGHT_DEFERRED` | **No** — the JSON API is directly fetchable with `requests`, no browser needed |

**Step 2 recommendation: do NOT write a new parser as part of this task (out of scope).** If a parser is written later, it should be a new `JSONSearchParser` subclass (e.g. `ShopifyPredictiveSearchParser`) that reads `resources.results.products[]` — a small, flat shape, notably simpler than `ShopifyParser`'s Elasticsearch-hits traversal. See §4 for the field mapping such a parser would need, and the "parser match" note below for why the existing `shopify` key cannot be reused as-is.

---

## 3. Sample request headers

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: application/json
Accept-Language: en-CA,en;q=0.9
```

No cookies or auth required for search (verified 2026-08-17). The response does set several `Set-Cookie` headers (`cart_currency`, `_shopify_y`, `_shopify_s`, etc.) — standard Shopify storefront session cookies, not required to be sent back for the search request to work.

For the HTML page (navigation only):

```http
Accept: text/html
```

---

## 4. Data selector map

Results come from JSON (a flat Shopify product list), so JSON paths are used rather than CSS selectors.

### Product container

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[]` | One entry per matching Shopify product (already deduplicated; not per-variant) |

### Title

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].title` | Full product title, e.g. `Chasing Tides Medium Roast` |
| `resources.results.products[].type` | Product type / category, e.g. `Coffee` |
| `resources.results.products[].vendor` | Storefront vendor label, e.g. `Just Us! Retail` |

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].price` | String, e.g. `"11.00"` — product-level display price |
| `resources.results.products[].price_min` / `price_max` | Same value when there's a single price point; would differ for multi-variant pricing |
| `resources.results.products[].compare_at_price_min` / `compare_at_price_max` | `"0.00"` when no compare-at price is set (not a real $0 price — needs a nonzero-check before use) |

Currency: response is served with `content-language: en-CA` and a `cart_currency=CAD` cookie is set; prices are plain decimal strings with no currency symbol or code embedded, so CAD must be assumed/configured rather than read from the payload.

### In stock

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].available` | Boolean, product-level aggregate across variants |

**Recommended instock rule:** `available == true`. There is no per-variant inventory count available from this endpoint (see caveat below), unlike `f2f` (`inventoryQuantity`) or `hfx` (`inventory_quantity`).

### Variant / condition

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].variants` | **Always `[]` in this endpoint's responses** — Shopify's predictive search does not include populated variant data. Confirmed in the live capture: all 3 products returned `"variants": []`. |

This is a real functional gap relative to the other three vendors' parsers, all of which read per-variant price/stock/condition. A `justus`-specific parser would only be able to report product-level price/availability, not per-variant (e.g. roast weight/grind) detail, unless a second API call per product (e.g. `/products/{handle}.js`) is added — out of scope for this investigation.

### HTML DOM (reference only — JS-rendered, not used for parsing)

| Field | CSS / element |
|-------|---------------|
| Results count (server-rendered) | `<meta property="og:title">`, plain text `"N results found for ..."` |
| Product grid (empty until JS hydration) | `#main-collection-product-grid` |
| Header predictive-search dropdown (empty until JS hydration) | `[data-js-search-results]` |

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `Chasing Tides` |
| `/search/suggest.json` products returned | **3** (`Medium`, `Light`, `Dark` roast) |
| HTML page `og:title` count | **3 results found** (matches API count for this query) |
| `resources[limit]` cap | 10 (Shopify hard cap for this endpoint; no pagination beyond it) |

---

## 6. Does `ShopifyParser` match this response? (No.)

`ShopifyParser.parse_data()` (`tracking/parsers.py`) expects:

```python
for hit in data.get("hits", {}).get("hits", []):
    src = hit.get("_source", {})
    title = src.get("title", "")
    ...
    for variant in src.get("variants", []):
        ...
```

i.e. it requires a top-level `hits.hits[]` array with an Elasticsearch `_source` wrapper, and it expects `variants[]` to be populated with `price`/`inventoryQuantity`/`selectedOptions`. Just Us! Coffee's `/search/suggest.json` response has **none of this**:

- Top-level key is `resources.results.products[]`, not `hits.hits[]`.
- No `_source` wrapper at all.
- `variants` is present but always `[]` — even if the path matched, the per-variant loop would silently produce zero results for every product.

**Conclusion: `ShopifyParser` (`parser_key: shopify`) does NOT match this response and must not be selected for this vendor.** This is exactly the trap flagged going into this investigation — `ShopifyParser` is named after the one third-party app (`prod-indexer`) that happens to return an Elasticsearch-shaped payload on a Shopify store, not a generic "any Shopify JSON" parser. Just Us! Coffee's response is Shopify's own **native** predictive-search shape, which is a different, simpler, and currently **unregistered** shape.

**No registered parser in `tracking/parsers.py` (`CCSearchParser`, `ShopifyParser`, `StorepassParser`, `WtFiltersParser`) matches `resources.results.products[]`.** Per this task's scope, no new parser is written here — see §2 and the fixture README's `parser_key` recommendation for how this gap should be recorded on the `Source` row.

---

## 7. Rate-limit signals

### Empirical

Checked 2026-08-17 via a single live `GET` to `justuscoffee.com/search/suggest.json` (exact request per §3 above, query `Chasing Tides`). Result: `200 OK` in ~0.40s. Full response header set observed:

```
date, content-type, x-download-options, x-xss-protection, set-cookie (×6),
x-content-type-options, search-engine, x-dc, report-to, nel,
shopify-complexity-score, shopify-complexity-score-v2, x-frame-options,
content-security-policy, strict-transport-security, vary (×2), content-language,
alt-svc, powered-by, server-timing, x-permitted-cross-domain-policies,
cf-cache-status, x-request-id, server, etag, cf-ray
```

None of `RateLimit-*`/`X-RateLimit-*` (IETF or `x-ratelimit` profiles) are present. No `extensions` key at all in the JSON body (top-level key is just `resources`), so no GraphQL-style `extensions.cost.throttleStatus` either — expected, since this is a REST/AJAX endpoint, not GraphQL.

One header worth flagging so it isn't mistaken for a rate-limit signal: `search-engine: elasticsearch`. This is Shopify's own internal infrastructure header (their predictive search is backed by Elasticsearch under the hood) — it is **not** evidence that the response shape matches `ShopifyParser`'s expected `hits.hits[]._source` format, and it is not a rate-limit header. Also present: `shopify-complexity-score` / `shopify-complexity-score-v2` — these look superficially like budget/cost headers but are Shopify Liquid rendering-complexity metrics (used internally for storefront performance monitoring), not a consumable request-budget signal in the `RateLimit-*`/`extensions.cost` sense.

This is one sample, not sustained-load testing — it shows no rate-limit signal is *advertised* on a normal request, not that the vendor/platform never throttles.

### Published platform documentation

Checked 2026-08-17. Unlike `f2f`/`hfx`/`wt` (all backed by unidentified or undocumented third-party apps), Just Us! Coffee's search endpoint **is** a documented, first-party Shopify API, and Shopify **does** publish throttling behavior for it specifically:

- **[Predictive Search API reference](https://shopify.dev/docs/api/ajax/reference/predictive-search)** (`shopify.dev/docs/api/ajax/reference/predictive-search`) states that exceeding the endpoint's request throttle returns an HTTP `429` with a `Retry-After` header (seconds to wait) and a JSON body of the form `{"status": "429", "message": "Too many requests", "description": "Throttled"}`. This is a real, endpoint-specific documented contract — not inferred from response shape.
- **This is a distinct rate-limit surface from Shopify's general Admin/Storefront-GraphQL/REST API limits** ([shopify.dev/docs/api/usage/limits](https://shopify.dev/docs/api/usage/limits)), which is explicitly scoped to *authenticated* API traffic (apps calling Shopify's GraphQL Admin API, REST Admin API, Storefront GraphQL API, etc. with an access token) — leaky-bucket buckets, points-per-second, etc. The predictive-search AJAX endpoint is unauthenticated public storefront traffic and is not covered by those documented point budgets; its own reference page's `429`/`Retry-After` behavior is the applicable contract instead.
- No specific numeric threshold (requests/second or /minute) is published for `/search/suggest.json` — only the existence of a throttle and the `429`/`Retry-After` response contract. The exact number would need to be discovered empirically (e.g. via sustained-load testing), which is out of scope here.

**Net effect on this Source's config:** unlike the other three vendors (where no applicable profile was found), Just Us! Coffee's endpoint has a **documented but header-less** throttle: no `RateLimit-*`-style headers to key a `rate_limit_profile` off of in advance, but a confirmed `429` + `Retry-After` **reactive** contract. None of the three currently registered `rate_limit_profile` values (`ietf`, `x-ratelimit`, `graphql_cost`) model a `Retry-After`-on-429 reactive pattern, so `rate_limit_profile` should still be left blank for now — but this vendor is a stronger argument than the other three for the app eventually supporting `Retry-After`-aware backoff, since Shopify has explicitly documented that behavior here.

---

## Appendix: Platform details

- **Platform:** Shopify (custom/bespoke theme — theme id `145019601058`, asset paths like `/cdn/shop/t/15/assets/...`; not an identifiably named public theme like Dawn/Empire).
- **Search backend:** Shopify's own native, first-party `/{locale}/search/suggest.json` Predictive Search API — no third-party search app detected (no Storepass/Klevu/Searchspring/etc. branding, script tags, or API hosts found while reviewing the search page's script includes).
- **Full-page search (`/search?q=...`)**: server-rendered shell with an empty product-grid `<div>`, hydrated client-side; result *count* is server-rendered via Liquid (`search.results_count`-style text/`og:title`), but individual product cards are not.
- **Currency:** CAD (`content-language: en-CA`, `cart_currency=CAD` cookie); prices returned as plain decimal strings with no currency unit.
- **Vendor label in data:** `Just Us! Retail` (as opposed to the org name "Just Us! Coffee" / co-op branding) — worth noting if `vendor` is ever used for display/matching.
