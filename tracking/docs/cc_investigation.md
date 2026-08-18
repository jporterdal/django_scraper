# Canada Computers — Search Investigation

**Date:** 2026-08-18
**Test query:** `RTX 5070`
**Investigator:** vendor investigation (automated fetch + manual DOM review)

---

## 0. Context — this vendor is different from the other seven

Every other vendor investigated so far (`f2f`, `hfx`, `wt`, `javablend`, `justus`, `mastermind`, `citadel`) is a JSON-API vendor: search results arrive either from a JSON endpoint fetched directly, or from an Alpine/React/wt-filters SPA layer sitting on top of one. `cc` (Canada Computers) is the odd one out — it is the app's **original vendor**, it is **currently live** in the `Source` table, and it is scraped in production today via `CCSearchParser`, a hand-rolled `HTMLParser` subclass (`tracking/parsers.py`) that walks the DOM with CSS-class-style checks, not a JSON parser. There is no JSON body to inspect for this vendor at all — see §3 for why the `extensions.cost` (GraphQL) check other docs run doesn't apply here.

Canada Computers is also the retailer itself (a PrestaShop storefront it operates directly), not a third-party SaaS platform (Shopify/Storepass/wt-filters) that other unrelated stores also run on. So the "published platform documentation" angle in this doc (§6) is about whether **Canada Computers** publishes anything, not about a platform vendor.

---

## 1. Search URL template

### Human-facing search page (browser) — same as the machine-facing one

```
https://www.canadacomputers.com/en/search?s={term}&pickup=62
```

Example: `https://www.canadacomputers.com/en/search?s=RTX%205070&pickup=62`

- Query parameter: `s`
- `{term}` replacement via `Source.build_search_url()` (`urllib.parse.quote_plus`) produces `RTX+5070` (or `%20`-style depending on encoding path); both are accepted by the storefront.
- Unlike every JSON-API vendor investigated so far, there is **no separate machine-facing API** — the exact same server-rendered PrestaShop page that a browser would show a human is what `CCSearchParser` parses. This is the simplest URL-template story of any vendor investigated: one URL, one surface, no SPA layer, no proxy app.

### The `pickup=62` parameter

`62` is a Canada Computers internal store ID. Passing `pickup={id}` on a search/category page tells the storefront to evaluate **in-store pickup availability specifically at that store** for every listed product, and to render a second stock line for it. This was confirmed empirically: fetching the live search URL (`s=RTX+5070&pickup=62`, 2026-08-18) shows each product's `.available-tag` block containing **two** stock lines per product —

```
Online - Available to Ship
In Store - Available for Pickup
```

— rather than one. `CCSearchParser.read_instock` specifically matches the phrase `"In Store - Available for Pickup"` (see §5) and ignores the "Online - Available to Ship" line; without `pickup={id}` in the URL, the storefront would have no specific store to evaluate that second line against, and the in-stock signal this parser is built around would likely not render the same way. In short: `pickup=62` is not a placeholder or leftover — it's the parameter that makes the "in stock at store X" data `CCSearchParser` reads even exist on the page.

I was not able to confirm which physical store `62` corresponds to without a further live request against an unidentified store-locator/geolocation endpoint (the public `/en/stores` page listing all locations uses a different, unrelated set of internal IDs in its own markup, and none of them was `62`). Given the instruction to keep live requests to this production vendor minimal, I did not chase this further — whoever configured this Source presumably picked a specific store deliberately (e.g. a warehouse/flagship location), but the identity of store `62` itself is not confirmable from the search page alone.

---

## 2. Rendering decision

| Check | Result |
|-------|--------|
| `requests.get` on the search URL returns product cards with prices | **Yes** — verified live 2026-08-18: `<span class="price no-sale-price" title="Price"> $1,139.99 </span>` present in the raw HTML response, no JS execution involved |
| View source contains static product prices | **Yes** |
| Products require browser JS | **No** — PrestaShop server-side rendered template (legacy `product-miniature`/`product-description` Smarty-derived markup) |
| JSON API returns structured products without JS | **N/A — no JSON API exists for this vendor** (see §0, §3) |

**Decision:** `HTML_PARSER`

| Subplan category | Applies? |
|------------------|----------|
| `HTML_PARSER` | **Yes** — this is what's actually implemented (`CCSearchParser`, `tracking/parsers.py`), and matches the empirical check above: full product data (title, price, stock) is present in the static HTML response, no rendering step required |
| `BEAUTIFULSOUP` | Would also work — same reasoning as `HTML_PARSER`; `CCSearchParser` instead builds directly on Python's stdlib `html.parser.HTMLParser` via `search_scrape.search_scrape.SearchParser`, avoiding the extra dependency |
| `PLAYWRIGHT_DEFERRED` | **No** — nothing about this storefront requires a headless browser |

This is the only vendor investigated so far where `HTML_PARSER` isn't a hypothetical alternative to a JSON route — it's the live, working implementation. Every other vendor's HTML search page turned out to be an empty shell (Alpine.js/React/wt-filter.js rendering from an API) or, at best (`citadel`, `javablend`), a viable-but-not-preferred alternative to a cleaner JSON API. Canada Computers has neither a discoverable nor even a suspected separate JSON API backing its search page — the HTML page is the only surface, and it's fully static.

---

## 3. Live response body — why the `extensions.cost` (GraphQL) check is not applicable

Every JSON-API vendor doc so far checks the response body's top-level keys for an `extensions.cost.throttleStatus` field (the `graphql_cost` rate-limit profile signal) alongside the `RateLimit-*`/`X-RateLimit-*` header checks. That check is **not applicable to `cc`** — there is no JSON response body at all. The live 2026-08-18 request returned `Content-Type: text/html; charset=utf-8`, and the payload is a ~479KB server-rendered HTML document, not JSON. There is no `extensions` key, no `cost` field, no GraphQL anywhere in this vendor's request/response cycle — noting this explicitly rather than leaving §7 blank, since the absence is structural (this vendor has no JSON surface to carry such a field), not a gap in the investigation.

---

## 4. Sample request headers

**Currently configured `request_headers`: `{}` (none).**

Live check (2026-08-18): a plain `requests.get()` with only a standard browser `User-Agent` set succeeded (`200 OK` on the search page). No `Accept`, `Origin`, `Referer`, cookies, or auth were required to get a full, correctly rendered results page back.

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
```

Two observations on the currently-configured *empty* `request_headers`:

- `Source.build_request_headers()` returns `None` when `request_headers` is falsy, so the `Fetcher` presumably falls back to its own default `User-Agent` (see `search_scrape.search_scrape.SearchParser.search()`, which hard-codes a Firefox UA — `Mozilla/5.0 ... rv:138.0 ... Firefox/138.0` — for the standalone entry point; the app's own `Fetcher` may use a different default, out of scope for this doc). Either way, **some** UA is being sent by the production scrape path already, or it wouldn't be working — this investigation didn't need to find *a* working UA, only confirm the site doesn't require anything beyond one.
- One data point worth flagging: a request to `/robots.txt` with **no** `User-Agent` header returned `403 Forbidden`; the identical request with a standard browser UA string returned `200 OK`. This suggests the storefront (or a WAF in front of it) does discriminate against headerless/non-browser-looking requests at least for some paths. This is a reason to keep sending a UA (which the live `Source` already implicitly relies on via the app's fetch layer) even though `request_headers` itself is empty — it's not evidence that more headers are needed beyond that.

**Conclusion:** the currently-configured empty `request_headers` looks correct/sufficient as long as the app's `Fetcher` sends *some* browser-like `User-Agent` outside of the per-Source `request_headers` JSON (which appears to be the case, since this Source is live and working in production). No `Accept`/`Origin`/`Referer` headers were found necessary.

---

## 5. Data selector map

Read directly from `CCSearchParser` in `tracking/parsers.py` (lines ~70–120), cross-checked against the live search page HTML (2026-08-18) and the fixture (`tracking/fixtures/html/cc/search_results_minimal.html`).

`CCSearchParser` inherits from `search_scrape.search_scrape.SearchParser`, which walks the DOM tag-by-tag using its own minimal `Element` wrapper (`tag`, `attrs`, `parent`) built on top of the stdlib `html.parser.HTMLParser`. Every `is_class(c)` check below is a **membership test against the element's `class` attribute split on whitespace** — it matches if `c` is *any one* of the element's classes, not an exact full-string match. This matters because the live site's actual markup (2026-08-18) carries longer, more specific class strings than the minimal test fixture uses, and the selectors still match correctly because of this membership semantics (see the "Live vs. fixture markup" note below each row).

### Product container

| Selector logic (`check_within_item_object`) | Notes |
|---|---|
| `element.tag == "div" and element.is_class("product")` | Any `<div>` carrying a `product` class token opens a new item. |

**Live vs. fixture markup:** the fixture uses `<div class="product">` (exact match). The live site (2026-08-18) uses `<div class="js-product product col-sm-6 col-xl-3" ...>` — a multi-token class string that still contains the bare token `product`, so `is_class("product")` still matches. Confirms the selector is written defensively (membership, not equality) and still works against real, evolved production markup.

### Title

| Selector logic (`check_element_title`) | Notes |
|---|---|
| current element `.tag == "a"` **and** its immediate `.parent.is_class("product-title")` | The title text is read from the `<a>` tag's text content, but the check fires based on the *parent* element carrying a `product-title` class. |
| `read_title(data)` | Just `str(data.strip())` — no regex extraction, unlike price. |

**Live vs. fixture markup:** fixture: `<div class="product-title"><a>Test GPU RTX 5070</a></div>`. Live site: `<h2 class="h3 product-title mb-0_5"><a href="...">ASUS Prime GeForce RTX 5070 12GB GDDR7 Graphics Card</a></h2>` — different tag (`h2` vs `div`) and multi-token class, but `is_class("product-title")` still matches by membership, and the parser doesn't care about the parent's tag name at all, only its class. Confirms the same defensive-matching pattern as the container selector.

### Price

| Selector logic (`check_element_price`) | Notes |
|---|---|
| current element `.tag == "span" and .is_class("price")` | |
| `read_price(data)` — regex `r".*\$([0-9\.\,]+)$"` applied to `data.strip()`, commas stripped, cast to `float` | Extracts the numeric amount following a literal `$` at the end of the string. Raises (logs an error, then re-raises) if the regex doesn't match at all — there is no silent fallback. |

**Live vs. fixture markup:** fixture: `<span class="price">$799.99</span>` (single class, price text with no extra whitespace). Live site: `<span class="price no-sale-price" title="Price"> $1,139.99 </span>` (multi-token class, and the actual text node is padded with substantial leading/trailing whitespace/newlines from the template — `data.strip()` in `read_price` handles that). The `$1,139.99` price includes a thousands-comma, which `read_price`'s `.replace(",", "")` handles correctly.

### In-stock

| Selector logic (`check_element_instock`) | Notes |
|---|---|
| current element `.tag == "b"` **and** it has *any* `<div>` ancestor (via `any_ancestor_tag("div")`) that `is_class("available-tag")` | Note this is **any** ancestor `div.available-tag`, not necessarily the immediate parent — the selector walks up the whole ancestor chain. |
| `read_instock(data)` | Strips the matched non-whitespace-bounded text (`re.match(r".*?(\S.*\S).*?", data, re.DOTALL)`) and compares it case-insensitively to the literal phrase `"In Store - Available for Pickup"`. Any other text (including the "Online - Available to Ship" line, or "Out of Stock") is treated as **not** in stock. |

**Live vs. fixture markup:** fixture has exactly one `<b>` inside `div.available-tag` per product (`In Store - Available for Pickup` or `Out of Stock`). The live site's `available-tag` block (2026-08-18) contains **two** `<b>` tags — one for "Online - Available to Ship", one for "In Store - Available for Pickup" (see §1's `pickup=62` discussion) — both nested a couple of levels deeper (inside `<a class="stock-popup">` → `<div class="line-height">` → `<small>` → `<b>`) than the flat fixture markup. Because `check_element_instock` checks *any* `div` ancestor rather than the immediate parent, both real `<b>` tags still qualify, and `self.instock` gets set twice in document order (first `False` from the "Online" line, then overwritten to `True` from the "In Store" line for an in-stock item) — the final value saved to the result row is whichever text was seen **last**, which in the live markup order happens to be the in-store/pickup line. This is a subtle but working interaction between the parser's "any ancestor" matching and the site's real two-line stock markup; the flat single-`<b>` fixture doesn't exercise this ordering interaction and a change to the live site's stock-line order could silently change results without changing the fixture at all.

### Title-pattern filtering (not a DOM selector, but part of the contract)

`CCSearchParserPatternTests` (`tracking/tests/test_parsers.py`) confirms `parser.title_patterns == [term.lower() + "$"]` after `_init_vars()` — i.e. the base `SearchParser.match_title()` anchors on the search term appearing at the **end** of a lowercased title. `CCSearchParser` doesn't override `title_patterns`/`match_title` with any GPU-specific variant patterns (the test name `test_cc_parser_no_gpu_patterns` reads as explicitly asserting the *absence* of any such special-casing).

---

## 6. Rate-limit signals

**Empirical, single live request** (2026-08-18, `GET https://www.canadacomputers.com/en/search?s=RTX%205070&pickup=62`, browser `User-Agent` only, no other headers — matching the currently-configured `Source` row exactly). One sample, healthy-path only — this can't surface load-triggered throttling, only confirm the absence of a signal on an ordinary request.

Result: `200 OK` in ~2.0s. Full response header set:

```
Date, Content-Type, Set-Cookie, Expires, Cache-Control, Pragma, Content-Encoding,
Vary, X-Frame-Options, Access-Control-Allow-Headers, Access-Control-Allow-Credentials,
X-XSS-Protection, X-Content-Type-Options, X-Content-Security-Policy,
Strict-Transport-Security, Transfer-Encoding
```

No `Server` header at all was disclosed (unlike `f2f`/`wt`, which both exposed `Server`/`CF-Ray` from a CDN in front of them — this response looks like it came straight from the PrestaShop origin, or from infrastructure that strips/omits the `Server` header).

None of `RateLimit`/`RateLimit-Limit`/`RateLimit-Remaining`/`RateLimit-Reset` (`ietf` profile) or `X-RateLimit-Limit`/`X-RateLimit-Remaining`/`X-RateLimit-Reset` (`x-ratelimit` profile) are present. As noted in §3, `extensions.cost.throttleStatus` (`graphql_cost` profile) is not applicable at all — there is no JSON body, so no `extensions` key can exist for this vendor.

**Recommendation:** leave `rate_limit_profile` blank/`none` — which matches the currently-configured live value. No vendor rate-limit signal of any registered profile was found. Continue relying on this app's fixed-delay pacing for this Source.

### Published platform documentation

Checked 2026-08-18.

Canada Computers is the retailer operating its own storefront (PrestaShop-based — confirmed by the generated `robots.txt` header comment: `# robots.txt automatically generated by PrestaShop e-commerce open-source solution`), not a third-party SaaS search/discovery platform that other, unrelated stores also run on. So unlike the Shopify-app-based vendors (`f2f`, `hfx`, `wt`), there's no separate "platform vendor" whose docs to check — the only question is whether Canada Computers itself publishes anything.

- **No official public API.** A web search for official Canada Computers developer/API documentation turned up nothing from canadacomputers.com itself. The only "API" reference found was a third-party, unofficial community project (a Devpost submission — a Flask-based scraper wrapped in a REST API, built by an outside developer against the same public storefront pages this investigation is looking at, not anything Canada Computers published or endorses).
- **`robots.txt` — no crawl-delay, and the search path is explicitly left crawlable.** Fetched `https://www.canadacomputers.com/robots.txt` live (browser UA; note a UA-less request to the same URL returned `403 Forbidden`, while the identical request with a standard browser `User-Agent` returned `200 OK` — see §4). It's a stock PrestaShop-generated file. It has no `Crawl-delay` directive and no `Sitemap` entry. It disallows the *legacy* query-param search form (`Disallow: /*?search_query=`, `/*controller=search`) but notably contains **commented-out** (`#`-prefixed, therefore inactive) rules for the friendly-URL search path this Source actually uses: `# Disallow: /*en/search` and `# Disallow: /*qc/search`. That is, whoever maintains this site's `robots.txt` had the option to block `/en/search` and left it commented out — the path this Source's `base_search_url` targets is not disallowed.
- **Net effect:** no rate-limit contract, published or otherwise, exists to configure against for this vendor — consistent with, and a second independent data point alongside, the empirical no-headers finding above.

---

## 7. Pagination

`CCSearchParser` mixes in `HTMLResponseParserMixin` (`tracking/parsers.py`), whose `next_page_url()` unconditionally returns `None` — "HTML parsers stay single-page." This is a hard-coded property of the parser, not a config choice: `CCSearchParser` has no pagination support at all today, regardless of `Source.max_pages`. The live `Source.max_pages` value is `1`, which is consistent with (and the only value that makes sense given) this limitation — raising `max_pages` for `cc` today would have no effect, since nothing in the parser knows how to build a page-2 URL yet.

(This differs from the JSON-API vendors: `ShopifyParser`/`StorepassParser` implement real `next_page_url` logic and are simply configured with `max_pages=1` as a deliberate choice; `WtFiltersParser.next_page_body` is a stub returning `None`, similar to `cc`'s situation. `cc` and `wt` are the two vendors investigated so far where single-page-only is a parser limitation rather than a dial.)

---

## 8. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Live-markup drift from the minimal fixture** — real product cards (2026-08-18) use different tag names/class strings than `search_results_minimal.html`, and selectors still match today only because of `is_class`'s membership semantics | Medium | Selectors are already written defensively; still worth periodically re-checking a live sample against the fixture's assumptions rather than assuming permanence |
| **`pickup=62` store dependency** — the in-stock signal this parser reads depends on a specific store ID baked into the URL; if that store closes/is renumbered, `read_instock`'s "In Store - Available for Pickup" match could silently stop firing | Medium | Document (this doc); periodically verify store `62` is still valid |
| **Two-line stock markup ordering** — `self.instock` gets overwritten by whichever of the two `<b>` stock lines appears last in document order (see §5); a markup reorder could flip results without any selector "breaking" in an obviously detectable way | Low–Medium | Add a regression fixture capturing the two-line live markup shape (out of scope for this investigation — see hard constraints) |
| **No fallback if price regex doesn't match** | Low | `read_price` re-raises on failure rather than silently dropping the product — fails loud, which is arguably the safer default already in place |
| **UA-sensitivity at the infra layer** — `robots.txt` itself 403s without a UA, hinting at basic bot-filtering in front of the site | Low | Already mitigated: this Source is live and working, meaning the app's fetch layer already sends a browser-like UA outside of the empty `request_headers` |
| **Single-page only** — no pagination support in `CCSearchParser` today | Low | Matches current `max_pages=1` config; would require new parser work to lift |

---

## Appendix: Platform details

- **Platform:** PrestaShop (self-hosted by Canada Computers; confirmed via `robots.txt` header comment and `PrestaShop-...` session cookie name)
- **Rendering:** fully server-side; no client-side JS framework involved in producing search results
- **Search entry point:** `/en/search?s={term}&pickup={store_id}` (French locale equivalent: `/qc/recherche?s={term}&pickup={store_id}`)
- **Currency:** CAD (prices rendered pre-formatted as `$X,XXX.XX` strings; `CCSearchParser.read_price` parses them back to `float`)
- **Session cookies observed:** `PHPSESSID`, `PrestaShop-<hash>` (encrypted), plus geo-derived location cookies (`hd_store_name`, `hd_iso_code`, `preferloc`) that reflect the requester's own apparent location and are unrelated to the explicit `pickup={id}` parameter
