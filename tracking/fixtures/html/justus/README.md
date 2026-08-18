# Just Us! Coffee — fixture & Source configuration

**Captured:** 2026-08-17
**Investigator:** automated fetch + manual DOM/API review
**Test query:** `Chasing Tides`

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured live response from `/search/suggest.json?q=Chasing+Tides&resources[type]=product&resources[limit]=10` (Shopify's native Predictive Search API) — 3 products returned |

## Source form values

**Not yet configured as a live Source in this app** — the values below are this investigation's recommendation for the "Add Source" form, not a copy of an existing row (none exists yet):

| SourceForm field | Value |
|---|---|
| `key` | `justus` |
| `name` | `Just Us! Coffee` |
| `parser_key` | *(none registered — needs a new parser; see investigation doc)* |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `GET` |
| `base_search_url` | `https://justuscoffee.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(none — GET request)* |
| `page_size` | `10` *(hard cap — see Pagination below; not adjustable upward)* |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json", "Accept-Language": "en-CA,en;q=0.9"}
```

**Why `parser_key` has no value:** the response shape is `resources.results.products[]` (Shopify's own native predictive-search shape), which does **not** match `ShopifyParser.parse_data()`'s expected `hits.hits[]._source...` Elasticsearch shape (that shape belongs to `f2f`'s bespoke `prod-indexer` app, not to Shopify generally). None of the four registered parsers (`cc`, `shopify`, `storepass`, `wtfilters`) match this response. Writing a new parser is out of scope for this investigation — see [tracking/docs/justus_investigation.md](../../../docs/justus_investigation.md) §6 for the shape comparison and §4 for the field mapping a future parser would need.

## Pagination

Shopify's Predictive Search API caps `resources[limit]` at **10** and has no page/offset/cursor parameter — this is a hard ceiling on the endpoint itself, not a `Source.max_pages` choice. A query matching more than 10 products cannot be paged through further via this endpoint (Shopify's own reference doc confirms "no more than 10 predictive suggestions per request type"). `max_pages` should stay at `1` regardless of how it's set, since there is no `next_page_url`/`next_page_body` mechanism this endpoint could support even in principle — this differs from `f2f` (`/page/N/` increments) and `hfx` (Storepass `current_page`/`pages`), both of which have real pagination that's merely *configured off* at `max_pages: 1`.

## Rate-limit signals

**Empirical: no rate-limit signal found in headers.** Checked 2026-08-17 with a single live GET to `/search/suggest.json` (exact request per the "Source form values" table above, query `Chasing Tides`). Result: HTTP 200 in ~0.40s. None of `RateLimit-*`, `X-RateLimit-*`, or a GraphQL `extensions.cost` block are present. (One header, `search-engine: elasticsearch`, looks rate-limit-adjacent but is not — it's Shopify's internal infra label for its predictive-search backend, unrelated to request budgets or to `ShopifyParser`'s response-shape expectations.) This is one sample, not sustained-load testing.

### Published platform documentation

Unlike `f2f`/`hfx`/`wt` (undocumented third-party apps), this endpoint **is** documented by Shopify: [shopify.dev/docs/api/ajax/reference/predictive-search](https://shopify.dev/docs/api/ajax/reference/predictive-search) confirms exceeding the throttle returns `429` with a `Retry-After` header. No specific requests/second number is published, only the existence of the throttle and its `429`/`Retry-After` contract — distinct from Shopify's authenticated Admin/Storefront-GraphQL rate-limit docs ([shopify.dev/docs/api/usage/limits](https://shopify.dev/docs/api/usage/limits)), which don't cover this unauthenticated AJAX endpoint. Recommendation: leave `rate_limit_profile` blank for now (none of the three registered profiles model a `Retry-After`-on-429 reactive pattern), but flag this vendor as the strongest current case for adding `Retry-After` support, since Shopify has explicitly documented it here.

## Refresh

```bash
python3 -c "
import json, requests
from pathlib import Path
from urllib.parse import quote_plus

term = 'Chasing Tides'
url = (
    'https://justuscoffee.com/search/suggest.json'
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
path = Path('tracking/fixtures/html/justus/search_results_sample.json')
path.write_text(json.dumps(data, indent=2) + '\n')
print('Wrote', path, 'products:', len(data['resources']['results']['products']))
"
```

See [tracking/docs/justus_investigation.md](../../../docs/justus_investigation.md) for the full investigation writeup (rendering decision, data selector maps, parser-shape mismatch discussion).
