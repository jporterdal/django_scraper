# Face to Face Games — fixture & Source configuration

**Captured:** 2026-07-07
**Investigator:** Phase 2 Step 1 (automated fetch + manual DOM review)
**Test query:** `Lightning Bolt`

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured API response for the test query above — live response from `/apps/prod-indexer/search/...` (24 products, 52 variant rows on the page, `hits.total.value` = 100) |
| `search_results_sample.html` | Synthetic static fragment documenting rendered card markup (not used for parsing; search page shell, rendering reference only) |

Note: the original capture also included `search_page_shell_stripped.html` (search page HTML with `<script>`/`<style>` removed, reference only). That file was deleted in commit `c01b64e` along with this README and is not restored here — it was reference-only and is not required by any test. Re-run the refresh flow below plus a plain HTML fetch of `https://facetofacegames.com/search?q={term}` if it's needed again.

## Source form values

The values below are copied from the **currently configured** `Source` row for key `f2f` in this app's database (verified 2026-08-17) — this is what the "Add/Edit Source" form should contain, not a re-derivation:

| SourceForm field | Value |
|---|---|
| `key` | `f2f` |
| `name` | `Face2Face` |
| `parser_key` | `shopify` |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `GET` |
| `base_search_url` | `https://facetofacegames.com/apps/prod-indexer/search/withFacets/false/pageSize/100/page/1/minimum_price/0.01/keyword/{term}` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(none — GET request)* |
| `page_size` | *(unset)* |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json", "Accept-Language": "en-CA,en;q=0.9"}
```

Note on `pageSize`: the fixture above was originally captured with `pageSize/24` (per the recovered original README's refresh script), yielding 24 hits per page / 52 variant rows. The currently configured `base_search_url` uses `pageSize/100`, per the investigation doc's recommendation ("use `pageSize=100` for fewer requests if the API allows") — this is a deliberate config choice made after capture, not a mismatch to fix. If the fixture is refreshed (see below), it will naturally pick up `pageSize/100` and hit counts will differ from the numbers quoted above.

## Pagination

`ShopifyParser.next_page_url` (`tracking/parsers.py`) increments the `/page/{n}/` URL segment via regex (`re.subn(r"(/page/)(\d+)", ...)`) each time it's asked for a next page, and returns `None` once a page's `response.json()["hits"]["hits"]` comes back empty — i.e. pagination stops naturally when the API runs out of results, not on a hardcoded page count.

The configured `max_pages` for this Source is `1`, so pagination is **configured off**, not unsupported — the parser is fully capable of following `/page/2/`, `/page/3/`, etc., but this Source is currently set to fetch only the first page (100 results, per `pageSize/100`) per search.

## Rate-limit signals

**No rate-limit signal found.** Checked 2026-08-17 with a single live GET to the `prod-indexer/search` endpoint (exact request per the "Source form values" table above — `term = "Lightning Bolt"`, same headers). This is one sample, not sustained-load testing — a vendor could still apply header-based limiting only once request volume climbs, which one request can't surface.

Result: HTTP 200 in ~0.28s. Full response header set:

```
Date, Content-Type, Transfer-Encoding, Connection, CF-Ray, CF-Cache-Status,
Access-Control-Allow-Origin, Cache-Control, Content-Encoding, Set-Cookie, Vary,
Access-Control-Allow-Credentials, Access-Control-Allow-Headers,
Access-Control-Allow-Methods, X-Download-Options, x-request-id, Report-To, Nel,
X-XSS-Protection, X-Content-Type-Options, X-Permitted-Cross-Domain-Policies,
Server, alt-svc
```

None of `RateLimit`, `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` (`ietf` profile) or `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (`x-ratelimit` profile) are present. The response is served straight off Cloudflare (`Server: cloudflare`, `CF-Ray`, `CF-Cache-Status: DYNAMIC`) with no CF rate-limit headers surfaced either.

The JSON body's top-level keys are `took`, `timed_out`, `_shards`, `hits`, `queryTier`, `searchParams`, `aggregations` — this is a raw Elasticsearch-style response, not GraphQL, and there is no `extensions` key at all, so no `extensions.cost.throttleStatus` (`graphql_cost` profile) either.

**Recommendation:** leave `rate_limit_profile` blank/unset (`none`) — no vendor rate-limit signal was found on this endpoint. Continue relying on this app's fixed-delay pacing rather than header-driven backoff for this Source. If Face to Face Games' API starts returning 429s or throttling in practice, that would be the trigger to re-check headers on an actual rate-limited response (which a single healthy-path sample like this one cannot provoke or reveal).

### Published platform documentation

Checked 2026-08-17, documentation-only (no live requests made for this subsection).

**(a) Shopify's own App Proxy rate limits — confirmed not applicable.** Shopify's App Proxy feature docs ([shopify.dev/docs/apps/build/online-store/app-proxies](https://shopify.dev/docs/apps/build/online-store/app-proxies)) contain no mention of rate limiting, throttling, or request limits for proxied traffic at all. Separately, Shopify's platform rate-limit docs ([shopify.dev/docs/api/usage/limits](https://shopify.dev/docs/api/usage/limits)) are explicitly scoped to apps calling Shopify's *own* GraphQL Admin API, REST Admin API, Storefront API, Payments Apps API, and Customer Account API — i.e. a third-party app authenticating and issuing requests *to* Shopify. Traffic under `/apps/{proxy-prefix}/...` is the reverse direction: Shopify's edge forwarding storefront-origin requests *out* to the third-party app's own backend. Neither doc claims the published Shopify rate limits govern that forwarded leg. Conclusion: Shopify's published API rate limits (e.g. GraphQL's 50/100/500 points-per-second leaky-bucket, REST's ~40 requests/60s) do **not** apply to this Source's traffic pattern — applying them here would be conflating two different API surfaces, which is the mistake this subsection is checking for.

**(b) Identity and published limits of "prod-indexer" — unconfirmed.** Could not identify which specific Shopify search/discovery app backs the `prod-indexer` proxy prefix. Searches for `"prod-indexer" shopify`, `"prod-indexer" api rate limit`, and the literal path `/apps/prod-indexer/search/withFacets` returned no matches (generic Shopify SEO-indexing apps like ProSEOIndexer, JIndex, etc. are unrelated — those are Google-indexing tools, not product search backends, and don't use this path). Checked several known Shopify search/discovery vendors (Klevu, Searchspring, Searchanise, Boost AI Search & Discovery) by name alongside distinctive query-string terms from this Source's URL (`withFacets`, `minimum_price`, `pageSize`) and found no association. The response body's raw Elasticsearch-shaped JSON (`took`, `_shards`, `hits`, `aggregations`, `queryTier`) suggests a custom or lightly-branded Elasticsearch-backed proxy rather than a well-known off-the-shelf app, but that is inference from response shape, not a confirmed identification. Because the specific app is unidentified, no app-specific published rate-limit documentation could be located either — there was nothing to search for once the identity search came up empty.

**(c) Net effect on this Source's config.** No documentation — Shopify-platform or vendor-specific — was found that establishes a rate limit applicable to this endpoint. This is consistent with, and adds a second independent data point alongside, the empirical no-headers finding above. Recommendation from the "Rate-limit signals" section (`rate_limit_profile` blank, rely on fixed-delay pacing) stands.

## Refresh

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path
from urllib.parse import quote_plus

term = 'Lightning Bolt'
url = (
    'https://facetofacegames.com/apps/prod-indexer/search'
    f'/withFacets/false/pageSize/100/page/1/minimum_price/0.01/keyword/{quote_plus(term)}'
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
path = Path('tracking/fixtures/html/f2f/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'hits:', len(data['hits']['hits']))
"
```

After refresh, run:

```bash
python manage.py test tracking.tests.test_investigations.F2FInvestigationTests tracking.tests.test_parsers.ShopifyParserFixtureTests tracking.tests.test_scrape_e2e.ShopifyScrapeE2ETests --settings=django_scraper.settings_test
```

Note the URL segment order and header set above match `base_search_url`/`request_headers` from the Source form values table — if the live `Source` config changes, update this script to match rather than the other way around. The `print('rate-limit headers: ...')` line is included so a future refresh doubles as a check for the still-unconfirmed rate-limit signals noted above.

See [tracking/docs/f2f_investigation.md](../../../docs/f2f_investigation.md) for the full investigation writeup (rendering decision, data selector maps).
