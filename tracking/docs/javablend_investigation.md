# Java Blend Coffee — Search Investigation

**Date:** 2026-08-17
**Test query:** `Ethiopia`
**Investigator:** automated fetch + manual DOM/API review

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://javablendcoffee.com/search?q={term}
```

Example: `https://javablendcoffee.com/search?q=Ethiopia`

- Query parameter: `q`
- `{term}` replacement via `Source.build_search_url()` (`urllib.parse.quote_plus`) produces `Ethiopia` unchanged for a single word; standard `+`-for-space encoding applies for multi-word queries.
- Unlike the two other JS-rendered vendors already investigated in this app (f2f, hfx), this page turned out to be **server-rendered static HTML** — see §2. It is a genuinely usable data source, not just a navigation shell.

### Machine-facing search API

```
https://javablendcoffee.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10
```

Example: `https://javablendcoffee.com/search/suggest.json?q=Ethiopia&resources%5Btype%5D=product&resources%5Blimit%5D=10`

This is Shopify's own **Predictive Search API** ([shopify.dev/docs/api/ajax/reference/predictive-search](https://shopify.dev/docs/api/ajax/reference/predictive-search)), a native, publicly documented, unauthenticated storefront endpoint — not a bespoke third-party app like f2f's `prod-indexer` or hfx's Storepass integration. It returned real product data for the test query (`Ethiopia Yirgacheffe`, `Ethiopia Ardi`, `Ethiopia Guji`), confirming the tip that "Ethiopia Ardi" is a real product on this store.

**Chosen endpoint for `base_search_url`: the Predictive Search API**, per the task's stated preference for the standard Shopify-native search endpoint when it "returns real product results" — which it does here. See §6 for why the static-HTML page (§1, human-facing) was investigated but not chosen as the primary source, despite also containing real data.

Verified findings about this endpoint:

- **`resources[limit]` is hard-capped at 10** regardless of the value requested (tested `10`, `50`, `100` — all returned exactly 10 rows for a query with more than 10 matches). This matches Shopify's published docs: *"The value can range from 1 to 10, and the default is 10."*
- **No pagination parameters exist.** Shopify's reference page documents no offset/page/cursor mechanism for this endpoint at all — `resources[limit]` is the only volume control, and it caps at 10. There is no way to page past the first 10 product hits for a broad query using this endpoint.
- **`resources[options][fields]` does not add variant data.** Passing `resources[options][fields]=title,variants.title,variants.sku,vendor,tag,product_type` returned `200 OK` but every product's `variants` array was still `[]`. `fields` controls which fields participate in *matching* the query, not which fields are populated in the response — the Predictive Search API does not return per-variant price/SKU/stock data at all. Only product-level `price`/`price_min`/`price_max`/`available` fields are populated.
- **`/products.json?title={term}` does NOT filter.** Tested: an unfiltered call to `/products.json?limit=250` and a call with `title=Ethiopia` both returned the same 76 products. The `title` query param is silently ignored by this native Shopify endpoint — it is not a real search filter, confirming the task brief's warning about this fallback.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL (`/search?q=...`) returns product cards with prices | **Yes** — static HTML, no JS execution needed |
| View source contains static product prices | **Yes** — `<span class="money">$20.00</span>` per product, plain server-rendered markup |
| Products require browser JS | **No** — page uses an old Shopify theme (`theme.version: '4.6.0'`) with a liquid snippet (`snippets/search-result.liquid`) rendered server-side |
| JSON API returns structured products without JS | **Yes** — `/search/suggest.json` (Predictive Search) returns JSON directly, no JS needed |

**Decision:** `JS_RENDERED_WITH_JSON_API` does not apply here — **neither surface requires JS**. This store is unlike f2f (Alpine.js) and hfx (Storepass React SPA): its search page is legacy server-rendered Liquid, and its JSON API is Shopify's own native, unauthenticated endpoint. Closest existing classification: **`HTML_PARSER`-eligible for the HTML page, plain unauthenticated JSON GET for the API** — both surfaces are viable without a headless browser.

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **Yes, for the `/search?q=` page** — static Liquid-rendered product cards with title, price, and link. No stock/availability signal present in this markup, however (see §6). |
| `BEAUTIFULSOUP` | Same as above — would work equally well as a traversal target. |
| `PLAYWRIGHT_DEFERRED` | **No** — nothing on this site requires JS rendering. |
| JSON API parser | **Yes, for `/search/suggest.json`** — but the JSON shape does **not** match any parser currently registered in `tracking/parsers.py`. See §6/§7. |

**Parser recommendation: none of the registered parsers fit as-is.** `ShopifyParser` expects an Elasticsearch-shaped body (`hits.hits[]._source...`); the Predictive Search API returns `resources.results.products[]` with flat product-level fields. `StorepassParser` and `WtFiltersParser` expect their own vendor-specific shapes and are unrelated. `CCSearchParser` is an HTML parser keyed to a different theme's DOM structure (`.product`/`.product-title`/`.price` classes with a specific "In Store - Available for Pickup" instock string) that does not match this store's markup either. **This is a genuine parser gap** — implementing one is out of scope for this investigation; see §7 for what a new parser would need to do.

---

## 3. Sample request headers

For the JSON API (`/search/suggest.json`):

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: application/json
Accept-Language: en-CA,en;q=0.9
```

For the HTML page (reference only — not the chosen endpoint):

```http
Accept: text/html,application/xhtml+xml
```

No cookies or auth required for either surface (verified 2026-08-17). The JSON response does set several first-party Shopify analytics cookies (`cart_currency`, `_shopify_y`, `_shopify_s`, `_shopify_essential`, etc.) via `Set-Cookie`, but none are required for the request to succeed — a fresh, cookie-less request returns the same product data.

---

## 4. Data selector map

### Product container (Predictive Search API)

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[]` | One entry per matched product. Capped at `resources[limit]` (max 10, see §1). |

### Title

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].title` | e.g. `Ethiopia Ardi` |
| `resources.results.products[].vendor` | Always `Java Blend` in the sample |

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].price` | String, e.g. `"20.00"` — the default/first-variant price |
| `resources.results.products[].price_min` / `price_max` | String range across variants, e.g. `"20.00"`/`"115.00"` for a product with multiple bag sizes |

**No per-variant price breakdown is available** — `variants` is always `[]` in this API's response (see §1). Only a single price or a min/max range at the product level.

### In stock

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].available` | Boolean, product-level. `Ethiopia Guji` in the sample is `available: false`. |

This is a genuine, reliable stock signal (unlike the HTML page — see below) — it is the strongest reason to prefer the JSON API over the static HTML search page for this vendor.

### Category

| JSON path | Notes |
|-----------|-------|
| `resources.results.products[].type` | e.g. `Coffee` — coarse product type, not a specific roast/origin category |
| `resources.results.products[].tags` | Array, e.g. `["Light Roast", "Micro-Lot Coffees", "Single Origin"]` — richer than `type` but multi-valued |

### HTML DOM (reference only — the static `/search?q=` page, not the chosen source)

| Field | CSS / structure |
|-------|------------------|
| Product container | `.wsgProductSelector.grid` (one per product, repeated with `<hr>` separators) |
| Title | `.grid__item.five-sixths p.h3--body a` |
| Price | `.grid__item.five-sixths h5 span.money` (text like `$20.00`) |
| Link | `a[href^="/products/"]` |
| **In stock** | **Not present in this markup at all.** The theme's JS config defines `soldOut`/`unavailable` strings, but the `search-result.liquid` snippet used for `/search` does not render any stock indicator per product — confirmed by searching the full response body: `"sold out"` appears exactly once, inside an unrelated `<script>` theme-strings block, never attached to a product card. |

This is the key reason the JSON API was chosen over the HTML page as the recommended `base_search_url`, despite the HTML page being genuinely static and scrapeable: **the HTML page cannot express in-stock/out-of-stock**, which this app's parsers need (`instock` is a required field in every registered parser's `add_result()`/`read_instock()`). The JSON API's `available` field fills that gap directly.

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `Ethiopia` |
| Predictive Search API results | **3** products (`resources[limit]=10` cap not reached for this narrow query) |
| Predictive Search API results, broader query (`coffee`) | **10** (hits the hard cap — true total is higher, unknowable via this endpoint) |
| Static HTML `/search?q=Ethiopia` page | **7** results (page text: "7 results"), no pagination links present |
| `/products.json` total catalog size | **76** products (unfiltered — confirms `title=` param does not filter, see §1) |

---

## 6. Comparing the two real candidate surfaces

Both the Predictive Search JSON API and the static HTML search page returned genuine, current product data for the same query — a choice was needed. Summary:

| | Predictive Search API (`/search/suggest.json`) | Static HTML (`/search?q=`) |
|---|---|---|
| Machine-readable | Yes (JSON) | Requires HTML parsing |
| Price | Yes (product-level, `price`/`price_min`/`price_max`) | Yes (single displayed price per card) |
| Title | Yes | Yes |
| **In stock** | **Yes (`available` boolean)** | **No — not rendered in this snippet at all** |
| Category/tags | Yes (`type`, `tags[]`) | No structured category, only free-text description |
| Result cap | Hard-capped at 10, no pagination | ~7 for this query; no visible pagination controls tested at scale, unconfirmed cap |
| Matches a registered parser shape | No (see §7) | No — nearest fit `CCSearchParser` still mismatches (different classes, different instock string) |
| Officially documented by platform | Yes (Shopify Ajax API reference) | No (this is a legacy, undocumented theme snippet specific to this store's currently installed theme) |

**Chosen: the Predictive Search API**, primarily because it has a working `instock` signal and is the officially documented, platform-native endpoint (matching the task's explicit preference), even though its 10-result cap with no pagination is a real limitation for broad queries.

---

## 7. Parser gap — recommendation for a future implementation

**No registered parser in `tracking/parsers.py` matches this response shape.** This section documents what a new parser would need; implementing it is out of scope for this investigation.

`ShopifyParser` (`tracking/parsers.py`) is hard-coded to the Elasticsearch-style shape used by f2f's bespoke `prod-indexer` app:

```python
for hit in data.get("hits", {}).get("hits", []):
    src = hit.get("_source", {})
    ...
    for variant in src.get("variants", []):
        ...
```

The Predictive Search API's actual shape is completely different — flat, product-level, no `hits`/`_source` wrapper, no populated `variants`:

```json
{
  "resources": {
    "results": {
      "products": [
        {
          "title": "Ethiopia Ardi",
          "price": "19.00",
          "price_min": "19.00",
          "price_max": "19.00",
          "available": true,
          "type": "Coffee",
          "tags": ["Light Roast", "Micro-Lot Coffees", "Single Origin"],
          "vendor": "Java Blend",
          "variants": []
        }
      ]
    }
  }
}
```

A new `JSONSearchParser` subclass (following the pattern of `WtFiltersParser`/`StorepassParser`) would need roughly:

```python
class ShopifyPredictiveSearchParser(JSONSearchParser):
    """Shopify native Predictive Search API (/search/suggest.json) results.

    One row per product (no variant-level breakdown available from this endpoint —
    price/availability are product-level only). Hard-capped at 10 results by the
    platform; no pagination exists for this endpoint (see javablend_investigation.md).
    """

    def parse_data(self, data):
        products = data.get("resources", {}).get("results", {}).get("products", [])
        for p in products:
            self.add_result(
                title=p.get("title", ""),
                price=p.get("price", 0),
                instock=p.get("available", False),
                category=p.get("type", ""),
            )

    def next_page_url(self, response, current_url, page_number):
        return None  # platform has no pagination for this endpoint
```

This is a genuinely new parser key (e.g. `shopify_predictive`), **not** a reuse of `parser_key: "shopify"` — using the existing `shopify` key against this response shape would silently produce zero results (`data.get("hits", {})` returns `{}` on this body, so `ShopifyParser.parse_data` iterates zero hits and the scrape would appear to "succeed" with no rows, which is worse than an explicit error). This mismatch is exactly the trap the task brief warned about.

---

## 8. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **No registered parser matches** | High | Documented here; do not set `parser_key: shopify` against this endpoint (see §7) |
| **Hard 10-result cap, no pagination** | Medium | Acceptable for narrow/specific queries (e.g. a known product name); broad queries will silently truncate at 10 with no way to see more via this endpoint |
| **No per-variant price/SKU breakdown** | Low | Product-level price only; fine for this vendor's catalog (coffee bags, not condition-graded singles like f2f/hfx) |
| **Legacy theme (`4.6.0`) could be replaced by the merchant** | Medium | Would break the HTML fallback path (§6) but not the JSON API, which is platform-level and independent of theme |
| **Static HTML page has no instock signal** | N/A (not chosen) | Documented as the reason the JSON API was preferred |

---

## Appendix: Platform details

- **Platform:** Shopify, `javablendcoffee.myshopify.com`
- **Theme:** Legacy theme, `theme.version: '4.6.0'`, third-party conversion-optimization plugin "wsg" (`wsgVersion: 6`) injected into `layout/theme.liquid`
- **Currency:** CAD (`currencyCode: "CAD"`, `content-language: en-CA`)
- **Search backend for `/search/suggest.json`:** Shopify's own platform-native Predictive Search (response header `search-engine: elasticsearch`, `powered-by: Shopify` — Shopify's own infrastructure, not a third-party app, unlike f2f/hfx)
- **Response headers of note:** `shopify-complexity-score`, `x-dc: gcp-us-east1`, served via Cloudflare (`server: cloudflare`, `CF-RAY`) — no rate-limit headers on a normal 200 response (see [tracking/fixtures/html/javablend/README.md](../fixtures/html/javablend/README.md) for the full rate-limit writeup)
