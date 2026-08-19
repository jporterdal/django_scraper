# HFX Games — Search Investigation

**Date:** 2026-07-07  
**Test query:** `Lightning Bolt`  
**Investigator:** vendor investigation (automated fetch + DOM/API review)

---

## 1. Search URL template

### Human-facing search page (browser)

```
https://hfxgames.com/search?q={term}
```

Example: `https://hfxgames.com/search?q=Lightning+Bolt`

- Query parameter: `q`
- `{term}` via `Source.build_search_url()` (`urllib.parse.quote_plus`) → `Lightning+Bolt` (verified).
- Alternate working URL: `https://hfxgames.com/search?type=product&q={term}`

### Machine-facing search API (actual product data)

HFX uses the **Storepass** SaaS search platform (React app loaded via `storepass.js`).

```
https://store.storepass.co/saas/search?store_id=Q5MjnQr1MA&name={term}&limit=30&sort=Relevance&mongo=true&override_buylist_gt_price=true&product_line=Magic%3A+the+Gathering
```

Recommended `base_search_url` for a future parser (MTG singles):

```
https://store.storepass.co/saas/search?store_id=Q5MjnQr1MA&name={term}&limit=30&sort=Relevance&mongo=true&override_buylist_gt_price=true&product_line=Magic: the Gathering
```

**Note:** `product_line` may need to be overridden per `ItemSource.url_suffix` for non-MTG product types (Pokemon, etc.). Store ID `Q5MjnQr1MA` comes from `search_settings.json` on the storefront.

Optional count endpoint (returns total without product rows):

```
https://store.storepass.co/saas/search?store_id=Q5MjnQr1MA&name={term}&with_count=true&no_track=true&product_line=Magic: the Gathering
```

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on HTML search URL returns product cards with prices | **No** — main content is empty `#product-listing-container` (hidden until React mounts) |
| View source contains static product prices | **No** — only 1 `$` price in full 1.1 MB HTML |
| Products require browser JS | **Yes** — Storepass React app renders into `#product-listing-container` |
| JSON API returns structured products without JS | **Yes** — `store.storepass.co/saas/search` returns JSON |

**Decision:** `JS_RENDERED_WITH_JSON_API`

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **No** |
| `BEAUTIFULSOUP` | **No** |
| `PLAYWRIGHT_DEFERRED` | **No** — Storepass API works with `requests` |

**Recommendation:** Implement a **JSON API parser** that fetches the Storepass search URL and parses `products[]` with per-condition rows from `variantInfo[]`.

---

## 3. Sample request headers

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: application/json
Origin: https://hfxgames.com
Referer: https://hfxgames.com/search?q=Lightning+Bolt
```

No cookies or auth required (verified 2026-07-07).

---

## 4. Data selector map

### Product container

| JSON path | Notes |
|-----------|-------|
| `products[]` | One Storepass product per Shopify listing (multiple condition variants) |

### Title

| JSON path | Notes |
|-----------|-------|
| `products[].display_name` | e.g. `Lightning Bolt [Beatdown]` — preferred for display |
| `products[].name` | e.g. `Lightning Bolt Beatdown` |
| `products[].vendor` | e.g. `Magic: The Gathering` — broad game/product-line signal |

### Price (CAD)

| JSON path | Notes |
|-----------|-------|
| `products[].variantInfo[].price` | Float per condition |
| `products[].variantInfo[].price_text` | e.g. `CA$3.00` |
| `products[].price_text` | Product-level list price |

### In stock

| JSON path | Notes |
|-----------|-------|
| `products[].variantInfo[].inventory_quantity` | Per condition; `> 0` = in stock |
| `products[].stock` | Product-level aggregate (may be 0 while variants have stock) |
| `products[].totalInventory` | Total across variants |

**Recommended instock rule:** `variantInfo[].inventory_quantity > 0` per variant row.

### Variant / condition

| JSON path | Notes |
|-----------|-------|
| `products[].variantInfo[].title` | `Near Mint`, `Lightly Played`, etc. |
| `products[].variantInfo[].sku` | e.g. `BTD-41-EN-NF-1` |
| `products[].productLineData` | MTG metadata (set, rarity, etc.) when present |

**item-category-relevance-filter:** `products[].vendor` (broad, e.g. `Magic: The Gathering`) feeds this app's `expected_product_line` check and the `product_line` column. `productLineData.set` (narrow, set-level) is what this app's own `category` field/column and `expected_category` check use, unchanged from before this capability.

**Naming collision — two different `product_line` concepts, same name, different layer.** This document's §1 already uses the literal string `product_line` as a **Storepass API query parameter** (e.g. `&product_line=Magic: the Gathering` baked into `base_search_url`/`url_suffix`, line 28/34/42 above) — a *request-side*, per-`ItemSource` concern that scopes which vendor product line the search itself queries against. The `item-category-relevance-filter` capability's `product_line` is a **different layer entirely**: a *response-side* filtering concern — an item-level `SearchableItem.expected_product_line` value checked against the `products[].vendor` signal in the *parsed response*, with the matched value persisted to `SearchResult.product_line`. The two are unrelated in code (one is a URL-building input on `Source`/`ItemSource`, the other is a filtering/persistence concern on `SearchableItem`/`SearchResult`/`JSONSearchParser`) and can disagree without either being wrong — e.g. the request-side parameter could scope a search to `Magic: the Gathering` while an item's `expected_product_line` independently narrows further, or targets a different vendor entirely for a multi-vendor `ItemSource` setup. See `design.md` Decision 6 under `openspec/changes/item-category-relevance-filter/` for the full rationale.

### HTML DOM (reference only — JS-rendered)

| Field | CSS / element |
|-------|---------------|
| Container | `#product-listing-container` |
| Product name | `.storepass-product-name` |
| Price | `.storepass-product-price` |
| Variant | `.storepass-variant-title` |

---

## 5. Sample product count (test query)

| Metric | Value |
|--------|-------|
| Query | `Lightning Bolt` (MTG product line) |
| OG meta title | **329 results found** (HTML page) |
| API count (`with_count=true`) | **111** (Storepass filtered count for MTG line) |
| Products per request (`limit=30`) | 30 |
| Variant rows per product | Up to 5 conditions (NM → Damaged) |

Pagination: Storepass returns `pages` and `current_page`; pass page parameters per Storepass API conventions (see `nextPageParameters` in responses).

---

## 6. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Third-party API** — Storepass (`store.storepass.co`) is undocumented | High | Pin fixture tests; monitor `FetchJob` errors |
| **Store ID coupling** — `Q5MjnQr1MA` embedded in URL | Medium | Hard-code in `Source.base_search_url` or migration; verify on deploy |
| **Product line parameter** — MTG vs Pokemon vs All | Medium | Use `ItemSource.url_suffix` for `product_line` override |
| **Count mismatch** — OG 329 vs API 111 | Low | Different filters/scopes; document which count the parser uses |
| **Large payloads** — 30 products ≈ 1.9 MB JSON | Medium | Tune `limit`; paginate |
| **Variant explosion** | Low | Use `ItemSource` pattern fields for foil/set filtering |

---

## 7. Recommended parser approach

**Base class:** Custom JSON parser — **not** `SearchParser` HTML traversal.

**Suggested `Source` row:**

```python
HFX_DEFAULT_SEARCH_URL = (
    "https://store.storepass.co/saas/search"
    "?store_id=Q5MjnQr1MA&name={term}&limit=30"
    "&sort=Relevance&mongo=true&override_buylist_gt_price=true"
    "&product_line=Magic: the Gathering"
)
```

**Parsing sketch:**

```python
for product in data.get("products", []):
    title = product.get("display_name") or product.get("name", "")
    for variant in product.get("variantInfo", []):
        results.append({
            "title": f"{title} ({variant['title']})",
            "price": float(variant["price"]),
            "instock": variant.get("inventory_quantity", 0) > 0,
            "category": product.get("productLineData", {}).get("set", ""),
        })
```

**Scrape integration:** May require POST-less GET fetch (compatible with current `Fetcher.get`). If `scrape._run_parser_search` only calls `parser.feed(html)`, override `feed()` to parse JSON or add a JSON branch.

**Fixtures:**

- Primary: `tracking/fixtures/html/hfx/search_results_sample.json`
- Reference: `tracking/fixtures/html/hfx/search_results_sample.html`

---

## Appendix: Platform details

- **Platform:** Shopify (Empire theme v12.1.1 by Out of the Sandbox)
- **Search provider:** [Storepass](https://store.storepass.co) — React SPA (`storepass.js`)
- **Settings:** `//hfxgames.com/cdn/shop/t/13/assets/search_settings.json`
- **Storepass store ID:** `Q5MjnQr1MA`
- **Currency:** CAD (`locale: en-CA` in search settings)
