# Mastermind Toys — Search Investigation

**Date:** 2026-08-17
**Test query:** `lego`
**Investigator:** vendor investigation (automated fetch + manual DOM/API review)

---

## 0. Vendor context

Mastermind Toys (mastermindtoys.com) is a national Canadian general toy retailer, added deliberately as a non-TCG vendor to broaden this app beyond trading-card-game shops. Its catalog does include some TCG product mixed in (a Pokémon TCG blister pack was the first hit on `/products.json` during initial scoping), but the test query below (`lego`) was chosen specifically to exercise the general-retail case this vendor represents, not another card-shop sample. All 10 results returned are LEGO sets — no TCG product in this sample.

Storefront platform: **Shopify** (confirmed via `powered-by: Shopify` response header and `cdn.shopify.com` asset URLs).

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://mastermindtoys.com/search?q={term}
```

Example: `https://mastermindtoys.com/search?q=lego`

Not used for scraping — see rendering decision below. Included here for reference only.

### Machine-facing search API (actual product data)

Mastermind Toys does **not** appear to run a bespoke third-party search/merchandising app (no Klevu, Searchspring, Algolia, or Bloomreach signal found in headers or body — see §6). It uses Shopify's own **native Predictive Search (AJAX) API**:

```
GET https://mastermindtoys.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10
```

Example (live, verified 2026-08-17):

```
https://mastermindtoys.com/search/suggest.json?q=lego&resources%5Btype%5D=product&resources%5Blimit%5D=10
```

- Query parameters: `q` (search term), `resources[type]=product` (restrict to products only, vs. also matching collections/pages/articles/queries), `resources[limit]` (results per type; **hard-capped at 10** by Shopify regardless of requested value — verified by requesting `resources[limit]=50` against query `puzzle` and receiving exactly 10 products back, status 200).
- No cookies or authentication required.
- Response `Content-Type: application/json; charset=utf-8`.
- Response headers include `search-engine: elasticsearch` and `powered-by: Shopify` — Shopify's predictive search is Elasticsearch-backed on Shopify's own infrastructure, but the **response body shape it returns to the client is Shopify's own predictive-search JSON contract, not a raw Elasticsearch `hits.hits[]._source` document** (see §4 and the parser gap noted in §2). "Elasticsearch under the hood" and "Elasticsearch-shaped response" are different claims — only the latter is what `ShopifyParser` needs, and this endpoint does not provide it.

`/products.json?title={term}` was **not** used — per the task's own endpoint-discovery guidance the Predictive Search API was tried first, worked cleanly on the first request, and is the standard documented Shopify mechanism for keyword search, so there was no need to fall back to probing whether `/products.json`'s `title` param genuinely filters.

**Note for a future step 2:** `base_search_url` should point at the `/search/suggest.json` API (not the HTML `/search` page) — the HTML search results page is client-side rendered from the same predictive-search data and has no static product prices in its initial HTML.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL returns product cards with prices | **No** — page shell only; results are fetched client-side |
| View source contains static product prices | **No** |
| Products require browser JS | **Yes**, for the `/search` HTML page — but irrelevant, since... |
| JSON API returns structured products without JS | **Yes** — `/search/suggest.json` returns full product JSON directly via plain `requests.get()`, no browser needed |

**Decision:** `JS_RENDERED_WITH_JSON_API`

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **No** — product rows are not in static HTML |
| `BEAUTIFULSOUP` | **No** — same reason |
| `PLAYWRIGHT_DEFERRED` | **No** — the JSON API is directly fetchable with `requests`, no rendering required |

**Parser recommendation — gap, not a fit:** `ShopifyParser` (`tracking/parsers.py`) is **not** a match for this response shape. `ShopifyParser.parse_data()` expects a raw Elasticsearch-style envelope: `data["hits"]["hits"][]["_source"]`, with `variants[].price`, `variants[].inventoryQuantity`, and `variants[].selectedOptions[]` per result (this is the shape used by `f2f`'s bespoke `prod-indexer` app, per `tracking/docs/f2f_investigation.md`). Mastermind's `/search/suggest.json` instead returns:

```
data["resources"]["results"]["products"][] = {
  "title": ..., "price": "34.99" (string),
  "available": true/false, "variants": [] (empty for this query — see §4),
  "type": ..., "tags": [...], "handle": ..., ...
}
```

No top-level `hits`/`_source` keys exist anywhere in the response. Feeding this JSON to `ShopifyParser.parse_data()` would call `data.get("hits", {}).get("hits", [])`, get an empty list back (the key doesn't exist), and silently produce **zero results** — not an exception, a silent no-op. This is exactly the kind of "shape looks Shopify-flavored, isn't the shape the parser wants" trap the task brief warned about.

**No registered parser in `tracking/parsers.py` matches this shape** (`CCSearchParser` is HTML-only; `StorepassParser` expects `data["products"][]` with `variantInfo[]`/`productLineData` — close in spirit but different field names and no `variantInfo` array here; `WtFiltersParser` expects `data["data"]["results"][]`). A new parser (e.g. `ShopifyPredictiveSearchParser`) reading `data["resources"]["results"]["products"][]` with fields `title`, `price` (needs `float()` cast — it's a string), `available` (already boolean), and `type` or a parsed `SUBCAT_*`/`MAINCAT_*` tag as category would be needed. **Writing that parser is out of scope for this task** — this is a gap to hand off, not fill.

---

## 3. Sample request headers

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: application/json
Accept-Language: en-CA,en;q=0.9
```

No cookies, `Origin`, `Referer`, or auth token required for a successful `200` (verified 2026-08-17). The response does set several first-party Shopify cookies (`cart_currency`, `_shopify_y`, `_shopify_s`, `_shopify_essential`, `_shopify_analytics`, `_shopify_marketing`) via `Set-Cookie`, but none are required on the request side to get valid results — a fresh, cookie-less request works.

---

## 4. Data selector map

Results come from JSON; use JSON paths, not CSS selectors.

### Product container

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[]` | One entry per matching Shopify product. Restricting to `resources[type]=product` excludes collections/pages/articles/query-suggestion entries that the same endpoint can also return. |

### Title

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].title` | Full product title, e.g. `LEGO® Lightning McQueen 77255` |
| `resources.results.products[].vendor` | Brand/manufacturer, e.g. `Lego Canada Inc.` — not part of title but useful for matching |

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].price` | **String**, e.g. `"34.99"` — needs `float()` cast, unlike `f2f`'s numeric `variants[].price` |
| `resources.results.products[].price_min` / `price_max` | Same value as `price` when the product has a single price point; would diverge for multi-variant products with a price range |
| `resources.results.products[].compare_at_price_min` / `compare_at_price_max` | Strike-through/original price; `"0.00"` when not on sale (all 10 sample products) |

Cookie `cart_currency=CAD` confirms CAD pricing for this request; no separate currency-rate multiplier observed in the response body.

### In stock

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].available` | Boolean, already resolved — `true` for all 10 sample products. No need to sum variant inventory like `f2f`'s `inventoryQuantity > 0` pattern. |

**Recommended instock rule:** use `available` directly (no derivation needed).

### Variant / condition

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].variants[]` | **Empty array (`[]`) for every product in this sample.** Shopify's predictive search only populates `variants[]` for products with option-level detail relevant to the suggestion UI; for these single-variant toy listings it's not populated. Do not assume variant-level rows the way `f2f`'s parser does — this vendor is effectively one row per product. |

### Category

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].type` | Shopify "product type" field — inconsistent casing observed (`LEGO®` vs `Lego`) even within one query's results, so treat as a rough signal, not a clean taxonomy |
| `resources.results.products[].tags[]` | Contains structured-looking tags like `MAINCAT_Building Sets`, `SUBCAT_Brick Sets`, `AGE_9 yrs`, `BRAND_LEGO® Speed Champions` — a merchant-defined tagging convention. A parser could parse out the `MAINCAT_`/`SUBCAT_` prefixed tags for a cleaner category than `type`, at the cost of extra string parsing. |

### HTML DOM (reference only — not used)

The `/search?q={term}` HTML page was not deeply inspected since the JSON API fully supersedes it for scraping purposes; no CSS selector map was built.

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `lego` |
| Products returned | **10** (the API's hard cap, confirmed by requesting `resources[limit]=50` on a separate query and still getting exactly 10 back) |
| TCG product in sample | **0 of 10** — all LEGO sets, confirming `lego` was a suitable non-TCG test query per the task brief |

No `total` or `count` field is present anywhere in the response body — the endpoint does not report how many total matches exist beyond the returned page, only the (capped) list itself.

---

## 6. Third-party search app check

Checked response headers and body for signals of a dedicated merchandising/search app commonly used by larger Shopify Plus retailers (Klevu, Searchspring, Algolia, Bloomreach) instead of native predictive search:

- No `X-Klevu-*`, `X-Searchspring-*`, `X-Algolia-*`, or Bloomreach-branded headers present.
- No distinct app proxy path segment (contrast with `f2f`'s `/apps/prod-indexer/...` or `wt`'s external `app-filters.wizardtower.com` host) — Mastermind's endpoint is a plain first-party Shopify path (`/search/suggest.json`), not an `/apps/{proxy}/...` app-proxy route.
- `powered-by: Shopify` and `search-engine: elasticsearch` headers, plus the exact `resources.results.products[]` response shape, positively match Shopify's own documented Predictive Search (AJAX) API contract (see §7), not a third-party vendor's.

**Conclusion:** this store uses Shopify's native search, not a bolted-on third-party search app. Unlike `f2f`/`wt`, there is no unidentified custom backend here — the platform is definitively known, which makes §7's rate-limit documentation check more conclusive than it was for those two vendors.

---

## 7. Rate-limit signals

**Empirical:** two live `GET` requests were made to `/search/suggest.json` during this investigation (query `lego`, then query `puzzle` with `resources[limit]=50` to test the limit cap) — both `200`, ~0.3s each. Full response header set observed:

```
Date, Content-Type, Transfer-Encoding, Connection, X-Download-Options, X-XSS-Protection,
Set-Cookie, X-Content-Type-Options, search-engine, x-dc, Report-To, Nel,
shopify-complexity-score, shopify-complexity-score-v2, x-frame-options,
content-security-policy, strict-transport-security, vary, Alt-Svc, content-language,
powered-by, server-timing, X-Permitted-Cross-Domain-Policies, cf-cache-status,
x-request-id, server, etag, content-encoding, CF-RAY
```

None of `RateLimit-*`/`X-RateLimit-*` (either the `ietf` or `x-ratelimit` header profiles) are present. There is no GraphQL `extensions.cost` block — this is not a GraphQL response (no `extensions` key at all). Two data points is not enough to characterize sustained-load behavior, consistent with the task's "single digits of requests" guidance — this cannot rule out throttling that only appears under higher volume.

One header worth flagging even though it isn't a rate-limit header: `shopify-complexity-score` / `shopify-complexity-score-v2` (`1140` / `114` observed) — this reflects Shopify's internal query-cost accounting for the request but is not documented as a client-facing throttle signal the way GraphQL's `extensions.cost.throttleStatus` is; treat as informational only.

### Published platform documentation

Checked 2026-08-17 via Shopify's own developer docs (`shopify.dev`).

**Predictive Search (AJAX) API reference** ([shopify.dev/docs/api/ajax/reference/predictive-search](https://shopify.dev/docs/api/ajax/reference/predictive-search)) confirms:
- Exceeding "the request throttle limit" returns **HTTP 429** with a `Retry-After` header (seconds to wait) — throttling is documented to exist, but **no specific numeric limit** (requests/second, requests/minute, etc.) is published for this endpoint.
- `resources[limit]` is documented as ranging `1`–`10` per resource type (default `10`) — matches the empirical cap found above.

**Storefront rate-limiting context** (per Shopify's general Storefront API / storefront-traffic documentation, corroborated via web search): Shopify does not publish fixed numeric limits for storefront page/AJAX traffic the way it does for GraphQL Admin API (points-per-second leaky bucket) or REST Admin API (~40 req/60s) — instead Shopify **rate-limits automated traffic (bots/crawlers) heuristically by buyer IP**, and explicitly notes the Storefront API "can not be utilized server-side or with a proxy" for exactly this reason (IP-based buyer identification). This is a meaningful caution for this app's use case: **scraping this endpoint from a shared server IP, at volume, is more likely to trip Shopify's bot-detection/throttling than a documented numeric quota would suggest** — the risk is real but not quantifiable from documentation alone.

Because this platform (native Shopify) is definitively identified — unlike `f2f`/`wt` where the backing app's exact identity remained unconfirmed — there was no need to search for a third-party vendor's rate-limit docs, and no separate app to conflate this doc with.

**Recommendation:** leave `rate_limit_profile` blank/unset — no numeric published limit exists to encode. Rely on this app's existing fixed-delay pacing, and treat the "server-side traffic risks heuristic throttling" documentation caveat as a reason to keep request volume and frequency conservative for this Source specifically, more so than for `f2f` (whose custom-app endpoint carried no equivalent documented server-side caution).

---

## 8. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Hard 10-result cap per query** — `resources[limit]` cannot exceed 10, and there is no pagination mechanism on this endpoint | High | Broad queries will silently truncate at 10 results with no `total` count to detect truncation against; document this ceiling clearly for any future `Source` config; consider narrower/paginated query strategies (e.g. per-collection) if full-catalog coverage matters |
| **No registered parser matches this shape** | High | Documented in §2; a new parser reading `resources.results.products[]` is needed before this vendor can be scraped — do not configure `parser_key: shopify` as-is |
| **Server-side/proxy traffic discouraged by Shopify docs** | Medium | Keep request cadence conservative; watch for 429 + `Retry-After`; treat any observed 429 as a hard signal, not a bug |
| **`variants[]` empty in observed sample** | Low | Don't rely on variant-level fields (`inventoryQuantity`, `selectedOptions`) the way the `f2f` parser does; use product-level `available`/`price` instead |
| **`price` is a string, not numeric** | Low | Cast with `float()` in any future parser, same pattern as other JSON parsers in this codebase |
| **Inconsistent `type` casing/format** (`LEGO®` vs `Lego`) | Low | Prefer parsing `MAINCAT_`/`SUBCAT_` tags for category if cleaner grouping is needed |

---

## Appendix: Platform details

- **Platform:** Shopify (native storefront, no app-proxy search backend identified)
- **Search backend:** Shopify's own Predictive Search (AJAX) API — `/search/suggest.json`, Elasticsearch-backed internally per `search-engine: elasticsearch` header, but with Shopify's own JSON response contract (`resources.results.products[]`), not a raw Elasticsearch hits envelope
- **Result cap:** 10 per query, no pagination, no total-count field
- **No CDN/WAF rate-limit headers observed**; platform docs confirm 429 + `Retry-After` exists but publish no numeric threshold
