# HFX Games — fixture & Source configuration

**Captured:** 2026-07-07
**Test query:** Lightning Bolt

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured Storepass API response for the test query above (MTG product line, 30 products) |
| `search_results_sample.html` | Synthetic static fragment documenting Storepass card markup, rendering reference only |

## Source form values

The values below are copied from the **currently configured** `Source` row for key `hfx` in this app's database (verified 2026-08-17) — this is what the "Add/Edit Source" form should contain, not a re-derivation:

| SourceForm field | Value |
|---|---|
| `key` | `hfx` |
| `name` | `HFXGames` |
| `parser_key` | `storepass` |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `GET` |
| `base_search_url` | `https://store.storepass.co/saas/search?store_id=Q5MjnQr1MA&name={term}&limit=30&sort=Relevance&mongo=true&override_buylist_gt_price=true&product_line=Magic: the Gathering` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(none — GET request)* |
| `page_size` | *(unset)* |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json", "Origin": "https://hfxgames.com", "Referer": "https://hfxgames.com/search?q=Lightning+Bolt"}
```

## Term relevance filtering

`JSONSearchParser.add_result` (`tracking/parsers.py`) now rejects any row whose title does not contain the search term as a contiguous, case/whitespace-normalized phrase, so `StorepassParser` drops off-term rows automatically. This is a shared base-class behavior verified against a live `wt` vendor response (see `tracking/fixtures/html/wt/README.md`) — `hfx` itself has only been checked against this captured fixture and a synthetic test case, not a live smoke test, since `search_results_sample.json` here (query `Lightning Bolt`) happens to contain only full-phrase-match rows. If this fixture is refreshed, ideally include at least one off-term row for regression coverage.

## Pagination

`StorepassParser.next_page_url` (`tracking/parsers.py`) reads `current_page` and `pages` from the JSON response body. If both are present and `current_page < pages`, it clones the current request URL and sets/replaces its `page` query parameter to `current_page + 1`; otherwise it returns `None` and the fetch stops. The fixture's captured payload carries `current_page`/`pages` — Storepass documentation also references a `nextPageParameters` field, but that key is absent from the observed response and is not relied on.

Note that the live `Source.max_pages` is `1`, so pagination is currently configured **off** for this vendor — this is a deliberate Source setting, not a limitation of the parser, which supports following additional pages if `max_pages` is raised.

## Rate-limit signals

**No rate-limit signal observed.** Checked 2026-08-17 via a single live GET to `store.storepass.co/saas/search` using the exact request config in the "Source form values" table above (response: `200 OK`, ~0.65s). This is one sample only — a vendor can apply header-based limiting only under sustained load, which a single request cannot reveal, so this is not proof the vendor never rate-limits, only that it advertises no signal on a normal request.

All response headers were inspected (`dict(resp.headers)`); none are rate-limit-related:
```
Access-Control-Allow-Headers, Access-Control-Allow-Methods, Access-Control-Allow-Origin,
Content-Length, Content-Security-Policy, Content-Type, Date, Etag, Nel, Report-To,
Reporting-Endpoints, Server, Set-Cookie, Via, X-Powered-By
```
Specifically absent: `RateLimit` / `RateLimit-Limit` / `RateLimit-Remaining` / `RateLimit-Reset` (IETF draft) and `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`.

The JSON body was also checked for a GraphQL-style cost block: no `extensions` key is present at all (top-level keys are `count`, `additional_results`, `products`, `current_page`, `pages`), confirming this REST-style endpoint carries no `extensions.cost.throttleStatus`.

**Recommendation:** leave `rate_limit_profile` blank/`none` for this Source. None of the three registered profiles (`ietf`, `x-ratelimit`, `graphql_cost`) apply based on this evidence — continue with fixed-delay pacing. If 429s are observed in production, re-check headers on that response specifically, since throttling here may be enforced silently (e.g. plain HTTP 429 with no advance signal) rather than advertised in advance.

### Published platform documentation

**No public developer documentation found for Storepass as of 2026-08-17**, which is consistent with (not contradicted by) the empirical no-header finding above — there's no published policy to cross-check against. Storepass (storepass.co, founded 2018) is a vertical B2B SaaS providing POS, buylist, inventory, pricing and storefront-search tooling to TCG/hobby-game retailers (hfxgames.com is one such storefront); its public site is aimed at shop owners considering the product, not third-party developers integrating against it. Searched: "Storepass API rate limit", "Storepass developer documentation API reference", "storepass.co" + rate limit/throttle/429/retry-after/requests-per-minute, "Storepass terms of service API usage", and "Storepass status page uptime". Findings:
- `storepass.co/support/documentation` exists but is end-user help content (FAQs and how-to videos for store owners), not an API/developer reference — no rate-limit, throttling, or endpoint documentation there.
- `status.storepass.co` is a component-uptime status page and does list an "Api" component (~98–99.9% 90-day uptime), but publishes no rate-limit policy, no `429`/`Retry-After` guidance, and no quota-header documentation.
- No dedicated developer portal, public API reference, or ToS clause addressing API rate limits turned up in any search; job postings confirm Storepass runs a RESTful API internally (Node.js/Express/MongoDB) but give no indication it's published for third-party use.

## Refresh

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path
from urllib.parse import urlencode

params = {
    'store_id': 'Q5MjnQr1MA',
    'name': 'Lightning Bolt',
    'limit': 30,
    'sort': 'Relevance',
    'mongo': 'true',
    'override_buylist_gt_price': 'true',
    'product_line': 'Magic: the Gathering',
}
url = 'https://store.storepass.co/saas/search?' + urlencode(params)
headers = {
    'User-Agent': 'Mozilla/5.0 (compatible; django_scraper fixture refresh)',
    'Accept': 'application/json',
    'Origin': 'https://hfxgames.com',
    'Referer': 'https://hfxgames.com/search?q=Lightning+Bolt',
}
data = requests.get(url, headers=headers, timeout=60).json()
path = Path('tracking/fixtures/html/hfx/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'products:', len(data.get('products', [])))
"
```

After refresh, run `python manage.py test tracking.tests.HFXInvestigationTests`.

See [tracking/docs/hfx_investigation.md](../../../docs/hfx_investigation.md) for the full investigation writeup (rendering decision, data selector maps).
