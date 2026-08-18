# Citadel Music — fixture & Source configuration

**Captured:** 2026-08-17
**Investigator:** automated fetch + manual DOM/API review
**Test query:** `pedal`

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured API response for the test query above — live response from `/search/suggest.json?q=pedal&resources[type]=product&resources[limit]=10` (10 products; Shopify's own Predictive Search API caps this endpoint at 10 results per resource type regardless of requested limit — verified by re-requesting with `resources[limit]=50` and still getting 10 back) |

## Source form values

**Not yet configured as a live Source in this app** — the values below are this investigation's recommendation for the "Add Source" form, not a copy of an existing row (none exists yet):

| SourceForm field | Value |
|---|---|
| `key` | `citadel` |
| `name` | `Citadel Music` |
| `parser_key` | *(none registered — needs a new parser; see investigation doc)* |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `GET` |
| `base_search_url` | `https://citadelmusichfx.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(none — GET request)* |
| `page_size` | `10` (hard Shopify-enforced ceiling on this endpoint — not a config choice, see Pagination below) |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json", "Accept-Language": "en-CA,en;q=0.9"}
```

**On `parser_key`:** the response shape (`resources.results.products[]`, string `price`, boolean `available`, always-empty `variants[]`) does **not** match `ShopifyParser.parse_data()` in `tracking/parsers.py`, which expects `hits.hits[]._source.variants[].price`/`.inventoryQuantity` (an Elasticsearch-hit shape used by f2f's bespoke third-party search app, not Shopify's own native endpoints). Running `ShopifyParser` against this fixture would iterate zero results (no `hits` key at all) rather than error — a silent failure mode, not a loud one. None of the other three registered parsers (`CCSearchParser`, `StorepassParser`, `WtFiltersParser`) match either. A new parser is needed; see `tracking/docs/citadel_investigation.md` Section 9 for a sketch. Writing it is out of scope for this investigation.

## Pagination

**Not supported by this endpoint at all**, unlike the other three configured vendors. Shopify's Predictive Search API (`shopify.dev/docs/api/ajax/reference/predictive-search`) documents `resources[limit]` as accepting `1`–`10` only, with no `page`/`offset`/cursor parameter — "the API returns no more than 10 predictive suggestions per request type." This was confirmed empirically: `resources[limit]=50` still returned exactly 10 products. The full catalog match count for the test query (`pedal`) was 23, per the HTML `/search?q=pedal` page's `.product-count__text` — so up to 13 true matches are simply unreachable through this endpoint for a broad query. A hypothetical future parser's `next_page_url` should unconditionally return `None` (there is nothing to increment). If exhaustive results become necessary later, the fallback would be Shopify's native paginated `/search?q={term}&page={n}` HTML page (confirmed server-rendered, no JS required — see investigation doc Section 2) or the authenticated Storefront GraphQL API, both out of scope here.

## Rate-limit signals

**Empirical:** checked 2026-08-17 across three live GETs to `/search/suggest.json` (`q=pedal` at `resources[limit]=10` and `=50`, plus `q=guitar` at `=10`). Full response header set for the first request:

```
Date, Content-Type, X-Download-Options, X-XSS-Protection, Set-Cookie (x5, _shopify_* tracking cookies),
X-Content-Type-Options, Search-Engine, X-Dc, Report-To, Nel, Shopify-Complexity-Score,
Shopify-Complexity-Score-V2, X-Frame-Options, Content-Security-Policy, Strict-Transport-Security,
Vary (x2), Alt-Svc, Content-Language, Powered-By, Server-Timing, X-Permitted-Cross-Domain-Policies,
Cf-Cache-Status, X-Request-Id, Server, Etag, Cf-Ray
```

None of `RateLimit-*` (`ietf` profile) or `X-RateLimit-*` (`x-ratelimit` profile) are present. The body is plain REST JSON (`resources.results.products`) with no `extensions` key, so no GraphQL `extensions.cost.throttleStatus` (`graphql_cost` profile) either. `Shopify-Complexity-Score`/`-V2` headers are present but appear to be internal Liquid-rendering telemetry, not one of this app's three modeled rate-limit profiles — treated as informational only, not actionable.

Note the `Search-Engine: elasticsearch` header — this is Shopify revealing its own backend infra choice, **not** a signal that the response body is Elasticsearch-hit-shaped (it isn't; see `parser_key` note above). Don't let this header suggest `parser_key: shopify` is correct.

Three healthy-path samples only, not sustained-load testing.

### Published platform documentation

Checked 2026-08-17. Unlike f2f (unidentified third-party app, no docs found) and hfx (Storepass SaaS, no public API docs found), this is Shopify's own first-party, documented endpoint:

- [Predictive Search API reference](https://shopify.dev/docs/api/ajax/reference/predictive-search) confirms exceeding the throttle returns **HTTP 429 with a `Retry-After` header** — but does not publish the exact numeric threshold.
- The same page documents the 10-result-per-type cap used above.
- [Shopify API usage limits](https://shopify.dev/docs/api/usage/limits) is scoped to authenticated Admin/GraphQL/Storefront API traffic (apps calling Shopify with credentials), not confirmed applicable to this unauthenticated storefront endpoint — same scoping caveat as noted in the f2f investigation.

**Recommendation:** leave `rate_limit_profile` blank — no header-based signal matches this app's three profiles. Rely on fixed-delay pacing. Unlike f2f/hfx, a genuine vendor-side throttle mechanism (429 + `Retry-After`) is confirmed to exist here by Shopify's own docs, even though its numeric value isn't published — if 429s appear in production, `Retry-After` on that response would be directly usable.

## Refresh

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path
from urllib.parse import quote_plus

term = 'pedal'
url = (
    'https://citadelmusichfx.com/search/suggest.json'
    f'?q={quote_plus(term)}&resources[type]=product&resources[limit]=10'
)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-CA,en;q=0.9',
}
resp = requests.get(url, headers=headers, timeout=30)
print('status:', resp.status_code)
print('rate-limit headers:', {k: v for k, v in resp.headers.items() if 'rate' in k.lower() or 'limit' in k.lower()})
data = resp.json()
path = Path('tracking/fixtures/html/citadel/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'products:', len(data['resources']['results']['products']))
"
```

No automated test suite currently references this vendor (no parser is registered for it yet — see `parser_key` note above), so there is no `manage.py test` invocation to run after a refresh.

See [tracking/docs/citadel_investigation.md](../../../docs/citadel_investigation.md) for the full investigation writeup (rendering decision, data selector maps, parser gap analysis).
