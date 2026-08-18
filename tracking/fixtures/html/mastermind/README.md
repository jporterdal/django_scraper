# Mastermind Toys — fixture & Source configuration

**Captured:** 2026-08-17
**Investigator:** automated fetch + manual DOM/API review
**Test query:** `lego`

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured API response for the test query above — live response from `GET /search/suggest.json?q=lego&resources[type]=product&resources[limit]=10` (10 products, all LEGO sets, no TCG product in this sample) |

## Source form values

**Not yet configured as a live Source in this app** — the values below are this investigation's recommendation for the "Add Source" form, not a copy of an existing row (none exists yet):

| SourceForm field | Value |
|---|---|
| `key` | `mastermind` |
| `name` | `Mastermind Toys` |
| `parser_key` | *(none registered — needs a new parser; see investigation doc)* |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `GET` |
| `base_search_url` | `https://mastermindtoys.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(none — GET request)* |
| `page_size` | `10` *(hard platform cap — see Pagination below)* |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json", "Accept-Language": "en-CA,en;q=0.9"}
```

**Important:** `parser_key: shopify` is **not** correct for this vendor despite the store running on Shopify. `ShopifyParser` (`tracking/parsers.py`) expects a raw Elasticsearch-style envelope (`data["hits"]["hits"][]["_source"]`), which is the shape used by `f2f`'s bespoke third-party `prod-indexer` search app — not the shape Shopify's own native Predictive Search API returns. Mastermind's response is `data["resources"]["results"]["products"][]`. Feeding this fixture to `ShopifyParser.parse_data()` would silently produce zero results (the `hits` key just doesn't exist — no exception is raised). See the investigation doc's §2 for the full comparison and what a correct parser would need to read instead.

## Pagination

The `/search/suggest.json` endpoint has **no pagination mechanism** — `resources[limit]` is hard-capped at 10 per resource type by Shopify itself (confirmed empirically: requesting `resources[limit]=50` still returned exactly 10 products), and there is no page/cursor/offset parameter and no `total` count field in the response to even detect truncation against. This is a platform limit, not a parser limitation — no parser, existing or hypothetical, can page past it on this endpoint. `max_pages: 1` reflects that there is only ever one page to fetch, not a configuration choice to fetch less than what's available.

## Rate-limit signals

**No rate-limit signal found in live headers.** Checked 2026-08-17 with two live GETs to `/search/suggest.json` (query `lego`, then `puzzle` with `resources[limit]=50` to probe the cap). Both `200` in ~0.3s. Full header set observed:

```
Date, Content-Type, Transfer-Encoding, Connection, X-Download-Options, X-XSS-Protection,
Set-Cookie, X-Content-Type-Options, search-engine, x-dc, Report-To, Nel,
shopify-complexity-score, shopify-complexity-score-v2, x-frame-options,
content-security-policy, strict-transport-security, vary, Alt-Svc, content-language,
powered-by, server-timing, X-Permitted-Cross-Domain-Policies, cf-cache-status,
x-request-id, server, etag, content-encoding, CF-RAY
```

None of `RateLimit-*`/`X-RateLimit-*` (`ietf` or `x-ratelimit` header profiles) present. No `extensions.cost` block — not a GraphQL response. `shopify-complexity-score`/`shopify-complexity-score-v2` headers are present (`1140`/`114` observed) but are Shopify's internal query-cost accounting, not a documented client-facing throttle signal. Two requests is not enough to characterize sustained-load behavior.

### Published platform documentation

Unlike `f2f`/`wt` (unidentified custom backend apps), this vendor's platform is **definitively Shopify's own native Predictive Search API** (`powered-by: Shopify` header, `search-engine: elasticsearch` header, response shape matches Shopify's documented contract exactly) — so there's no third-party-vendor identity question here, and Shopify's own docs are the right (and only) place to check.

Shopify's Predictive Search (AJAX) API reference ([shopify.dev/docs/api/ajax/reference/predictive-search](https://shopify.dev/docs/api/ajax/reference/predictive-search)) confirms exceeding "the request throttle limit" returns **HTTP 429 with a `Retry-After` header**, but publishes **no specific numeric limit** (no requests/second or requests/minute figure). Separately, Shopify's storefront-traffic documentation notes Shopify heuristically rate-limits **automated/bot traffic by buyer IP** on storefront and AJAX endpoints, and explicitly discourages server-side/proxied use of these endpoints for that reason — a caution worth taking seriously for this app's scraping use case even though it's not a numeric quota.

**Recommendation:** leave `rate_limit_profile` blank/unset — no numeric published limit exists to encode against a profile. Rely on this app's existing fixed-delay pacing, and keep request volume/frequency conservative given Shopify's documented (if non-numeric) caution against server-side automated traffic on this endpoint class.

## Refresh

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path

term = 'lego'
url = 'https://mastermindtoys.com/search/suggest.json'
params = {'q': term, 'resources[type]': 'product', 'resources[limit]': 10}
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-CA,en;q=0.9',
}
resp = requests.get(url, params=params, headers=headers, timeout=30)
print('status:', resp.status_code)
print('rate-limit headers:', {k: v for k, v in resp.headers.items() if 'rate' in k.lower() or 'limit' in k.lower()})
data = resp.json()
path = Path('tracking/fixtures/html/mastermind/search_results_sample.json')
path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
print('Wrote', path, 'products:', len(data['resources']['results']['products']))
"
```

See [tracking/docs/mastermind_investigation.md](../../../docs/mastermind_investigation.md) for the full investigation writeup (rendering decision, data selector maps, parser gap analysis).
