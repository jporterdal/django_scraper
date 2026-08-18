# Java Blend Coffee — fixture & Source configuration

**Captured:** 2026-08-17
**Investigator:** automated fetch + manual DOM/API review
**Test query:** `Ethiopia`

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured Shopify Predictive Search API response (`/search/suggest.json`) for the test query above — live response, 3 products (`Ethiopia Yirgacheffe`, `Ethiopia Ardi`, `Ethiopia Guji`) |

## Source form values

**Not yet configured as a live Source in this app** — the values below are this investigation's recommendation for the "Add Source" form, not a copy of an existing row (none exists yet):

| SourceForm field | Value |
|---|---|
| `key` | `javablend` |
| `name` | `Java Blend Coffee` |
| `parser_key` | *(none registered — needs a new parser; see investigation doc, §7)* |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `GET` |
| `base_search_url` | `https://javablendcoffee.com/search/suggest.json?q={term}&resources[type]=product&resources[limit]=10` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(none — GET request)* |
| `page_size` | `10` *(hard platform cap — see Pagination below)* |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json", "Accept-Language": "en-CA,en;q=0.9"}
```

**Important — `parser_key` cannot be set to `shopify` for this vendor.** `ShopifyParser` (`tracking/parsers.py`) expects an Elasticsearch-shaped body (`hits.hits[]._source.variants[]...`), which is the shape used by f2f's bespoke third-party search app. This vendor's endpoint is Shopify's own **native** Predictive Search API and returns a completely different shape: `resources.results.products[]`, flat product-level fields, `variants` always empty. Pointing `ShopifyParser` at this response would not error — `data.get("hits", {})` just returns `{}`, so the parser would silently produce zero results. Leave `parser_key` unset/document the gap until a dedicated parser is written (see [tracking/docs/javablend_investigation.md](../../../docs/javablend_investigation.md), §7, for a sketch of what that parser needs to do).

## Pagination

The Predictive Search API has **no pagination mechanism at all** — confirmed both empirically (tested `resources[limit]` of 10, 50, and 100; all three returned exactly 10 rows, the value never grew past 10) and against Shopify's own published reference (`resources[limit]` documented range is "1 to 10, default 10"; no offset/page/cursor parameter is documented for this endpoint). A query with more than 10 matches is truncated with no way to retrieve the remainder through this endpoint. `max_pages` should stay at `1` regardless of configuration — there is nothing for a `next_page_url` to increment, unlike `ShopifyParser`'s `/page/{n}/` handling for f2f.

## Rate-limit signals

**Checked 2026-08-17, based on a single live sample.** One live GET was made to `https://javablendcoffee.com/search/suggest.json` using the exact request config in the "Source form values" table above (`q=Ethiopia`). Result: `200 OK` in ~0.29s. This is one sample only — a vendor can apply throttling only under sustained load, which a single request cannot reveal.

Full response header set observed:
```
Date, Content-Type, Transfer-Encoding, Connection, X-Download-Options, X-XSS-Protection,
Set-Cookie, X-Content-Type-Options, search-engine, x-dc, Report-To, Nel,
shopify-complexity-score, shopify-complexity-score-v2, x-frame-options,
content-security-policy, strict-transport-security, vary, Alt-Svc, content-language,
powered-by, server-timing, X-Permitted-Cross-Domain-Policies, cf-cache-status,
x-request-id, server, etag, content-encoding, CF-RAY
```

None of `RateLimit`/`RateLimit-Limit`/`RateLimit-Remaining`/`RateLimit-Reset` (`ietf` profile) or `X-RateLimit-Limit`/`X-RateLimit-Remaining`/`X-RateLimit-Reset` (`x-ratelimit` profile) are present. The JSON body's only top-level key is `resources` (no `extensions` key at all), so no `extensions.cost.throttleStatus` (`graphql_cost` profile) either — this is a plain REST-style response, not GraphQL.

Notably present: `shopify-complexity-score` (`1420`) and `shopify-complexity-score-v2` (`142`) — these reflect Shopify's internal storefront-rendering cost accounting, not a documented client-facing rate-limit budget signal, and don't correspond to any of this app's three registered profiles.

**Recommendation:** leave `rate_limit_profile` blank/unset (`none`) — no proactive vendor rate-limit signal was found on this endpoint, consistent with the other three vendors already investigated in this app. Continue relying on this app's fixed-delay pacing. If 429s are observed in production, note that Shopify's own docs (see below) confirm this endpoint *does* enforce a throttle reactively — it just isn't advertised in advance on a normal response — so a 429 should be treated as a real signal and handled via `Retry-After` (already-existing generic 429/Retry-After handling in `tracking/scrape.py`/`tracking/fetcher.py`, separate from the `rate_limit_profile` mechanism).

### Published platform documentation

Checked 2026-08-17 via Shopify's own developer docs ([shopify.dev/docs/api/ajax/reference/predictive-search](https://shopify.dev/docs/api/ajax/reference/predictive-search)) — this is a genuinely Shopify-native endpoint (unlike f2f's unidentified third-party app or hfx's Storepass SaaS), so, unlike those two, platform documentation for this specific endpoint does exist and is directly applicable:

- **Confirmed applicable:** "Exceeding the request throttle limit will return a `429` status code with a relevant error message," and the error response carries a `Retry-After` header (value in seconds). This is a genuine, documented, endpoint-specific rate-limit contract — unlike f2f (undocumented third-party app, no findable identity) and hfx (Storepass, no public developer docs at all), this vendor's throttle behavior is explicitly published by the platform itself.
- **Not documented:** the specific requests-per-minute/hour threshold before a 429 is triggered. Shopify's reference describes the *existence* and *shape* of the throttle response, not its numeric budget.
- **Net effect:** none of this app's three registered `rate_limit_profile` values (`ietf`, `x-ratelimit`, `graphql_cost`) fit — this endpoint's documented behavior is a reactive 429+`Retry-After` pattern with no proactive budget headers on healthy requests, which matches the empirical finding above exactly. `rate_limit_profile` should stay blank; the existing generic `Retry-After`-on-429 handling in this app's fetch/scrape pipeline (not the `rate_limit_profile` registry) is the correct mechanism for this vendor if/when a 429 is actually hit.

## Refresh

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path

term = 'Ethiopia'
url = 'https://javablendcoffee.com/search/suggest.json'
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
path = Path('tracking/fixtures/html/javablend/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'products:', len(data['resources']['results']['products']))
"
```

Note the URL/params and header set above match the `base_search_url`/`request_headers` recommended in the "Source form values" table — since no live `Source` row exists yet for this vendor, this script itself is the reference until one is created. The `print('rate-limit headers: ...')` line doubles as a check for the still-only-reactive rate-limit signal documented above.

See [tracking/docs/javablend_investigation.md](../../../docs/javablend_investigation.md) for the full investigation writeup (rendering decision, data selector maps, and why the JSON API was chosen over the also-static HTML search page).
