# Canada Computers — fixture & Source configuration

**Captured:** predates investigation-doc convention. The fixture was added in commit `50f53a0` ("+feat: phase 1 complete, mvp", 2026-07-07) — the same commit that introduced `CCSearchParser` and its test suite — and its content is hand-authored synthetic markup (product titles are literally `Test GPU RTX 5070` and `Other Product`), not a saved copy of a real Canada Computers response. `git log --follow` on the file shows only that one commit; there is no earlier "live capture" to date.
**Test query:** `RTX 5070` — the term used by `CCSearchParserFixtureTests`/`CCSearchParserPatternTests`/`ParserContractTests` (`tracking/tests/test_parsers.py`) when parsing this fixture; it doesn't need to match the fixture's product titles since the fixture is synthetic and the tests assert on selector/parsing behavior, not term-matching.

## Files
| File | Purpose |
|------|---------|
| `search_results_minimal.html` | Existing hand-authored HTML fixture (2 synthetic products: one in-stock, one out-of-stock) used by `CCSearchParserFixtureTests`, `CCSearchParserPatternTests`, and `ParserContractTests` — **not modified by this investigation**, per hard constraint (it backs currently-passing production tests) |

## Source form values

**Currently configured as a live Source in this app** (verified 2026-08-17 — this is the app's original, production vendor, and the only one of the eight investigated vendors that is actually live today):

| SourceForm field | Value |
|---|---|
| `key` | `cc` |
| `name` | `Canada Computers` |
| `parser_key` | `cc` |
| `rate_limit_profile` | *(blank)* |
| `http_method` | `GET` |
| `base_search_url` | `https://www.canadacomputers.com/en/search?s={term}&pickup=62` |
| `request_headers` | `{}` (none configured) |
| `request_body_template` | `{}` (GET request, not applicable) |
| `page_size` | *(unset)* |
| `max_pages` | `1` |

`base_search_url` matches the `CC_DEFAULT_SEARCH_URL` constant already defined in `tracking/models.py`. See [tracking/docs/cc_investigation.md](../../../docs/cc_investigation.md) §1 for what the baked-in `pickup=62` query param does (short version: it's a Canada Computers internal store ID that makes the storefront render a second, store-specific "In Store - Available for Pickup" stock line per product — the exact phrase `CCSearchParser.read_instock` matches on).

## Pagination

`CCSearchParser` mixes in `HTMLResponseParserMixin`, whose `next_page_url()` unconditionally returns `None` — HTML parsers in this app stay single-page by design, not by configuration. This is a hard parser limitation, not a dial: the live `Source.max_pages` value of `1` is the only value that has any effect today, since nothing in `CCSearchParser` knows how to construct a page-2 URL. (Contrast with `ShopifyParser`/`StorepassParser`, which implement real pagination and are simply configured with `max_pages=1` as a deliberate choice.)

## Rate-limit signals

**Empirical finding from one live GET** to the real `base_search_url` (2026-08-18, `https://www.canadacomputers.com/en/search?s=RTX%205070&pickup=62`, browser `User-Agent` only — no other headers, matching the currently-configured empty `request_headers`). One sample, healthy-path only.

Result: `200 OK` in ~2.0s. Full response header set observed:

```
Date, Content-Type, Set-Cookie, Expires, Cache-Control, Pragma, Content-Encoding,
Vary, X-Frame-Options, Access-Control-Allow-Headers, Access-Control-Allow-Credentials,
X-XSS-Protection, X-Content-Type-Options, X-Content-Security-Policy,
Strict-Transport-Security, Transfer-Encoding
```

No `Server` header was disclosed at all (this response looks like it's served straight from the PrestaShop origin, unlike `f2f`/`wt` which both sit behind a CDN that discloses itself).

None of `RateLimit`/`RateLimit-Limit`/`RateLimit-Remaining`/`RateLimit-Reset` (`ietf` profile) or `X-RateLimit-Limit`/`X-RateLimit-Remaining`/`X-RateLimit-Reset` (`x-ratelimit` profile) are present.

**`extensions.cost` (`graphql_cost` profile) — not applicable.** Unlike every JSON-API vendor investigated so far, `cc` has no JSON response body at all: `Content-Type` is `text/html; charset=utf-8`, and the payload is a server-rendered HTML page (the same page a browser would show). There is no `extensions` key to check because there is no JSON body in which one could exist — this isn't an unchecked gap, it's structurally not applicable to an HTML-parsed vendor.

**Recommendation:** leave `rate_limit_profile` blank/`none` — which matches the currently-configured live value. No vendor rate-limit signal was found. Continue relying on this app's fixed-delay pacing for this Source.

### Published platform documentation

Checked 2026-08-18. Canada Computers is the retailer itself (self-hosted PrestaShop), not a third-party SaaS platform other stores also run on, so — unlike the Shopify-app vendors — there's no separate platform vendor whose docs apply here; the only question is whether Canada Computers itself publishes anything.

**Found: nothing.** A web search for official Canada Computers developer/API documentation turned up no public API or developer terms published by canadacomputers.com — only an unrelated, unofficial third-party scraper project (a community Devpost submission), not anything Canada Computers published or endorses. `robots.txt` (fetched live 2026-08-18) is a stock PrestaShop-generated file with no `Crawl-delay` directive and no `Sitemap`; it disallows the legacy `?search_query=`/`controller=search` URL forms but contains explicitly **commented-out** (inactive) rules for the friendly-URL path this Source actually uses (`# Disallow: /*en/search`) — i.e. `/en/search` is not disallowed. One incidental finding: requesting `/robots.txt` with no `User-Agent` header returned `403 Forbidden`, while the identical request with a standard browser UA returned `200 OK` — basic bot-filtering exists at the infra layer, but it's UA-presence-based, not a documented rate-limit contract. Net: no published rate-limit or API documentation exists to configure against, consistent with the empirical no-headers finding above.

## Refresh

N/A in the sense the other vendors' `Refresh` sections mean it — there's no JSON capture script to re-run, because this fixture is hand-authored synthetic HTML, not a saved copy of a live response (see `Captured` above). If `search_results_minimal.html` ever needs replacing (e.g. because `CCSearchParser`'s selectors are changing and the fixture should be updated in lockstep — see the comment at the top of the fixture file itself), that would mean either:

- hand-editing the synthetic HTML to match the new selector shape (as today), or
- saving a fresh, trimmed copy of a real rendered `https://www.canadacomputers.com/en/search?s={term}&pickup=62` response (per §1/§5 of the investigation doc for the live markup shape as of 2026-08-18) and reducing it down to a couple of representative in-stock/out-of-stock product blocks.

Either way, this is out of scope for this investigation — the fixture is explicitly not to be modified here (it backs currently-passing tests: `CCSearchParserFixtureTests`, `CCSearchParserPatternTests`, `ParserContractTests` in `tracking/tests/test_parsers.py`).

See [tracking/docs/cc_investigation.md](../../../docs/cc_investigation.md) for the full investigation writeup (rendering decision, data selector map, live-vs-fixture markup comparison).
