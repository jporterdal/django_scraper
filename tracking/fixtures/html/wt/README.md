# Wizard's Tower — fixture & Source configuration

**Captured:** 2026-07-07
**Investigator:** vendor investigation (automated fetch + DOM/API review)
**Test query:** Lightning Bolt

## Files
| File | Purpose |
|------|---------|
| `search_results_sample.json` | Captured wt-filters `POST /api/search` response for the test query above (24 results, page 1) |
| `search_results_sample.html` | Synthetic static fragment documenting product card fields, rendering reference only |
| `search_results_fire_dragon.json` | Captured `POST /api/search` response for `term="Fire Dragon"` (24 results, page 1; 3,646 vendor-reported total). Only 1 of the 24 rows is the genuine MTG card — the rest include four listings of `"Dragon Fire"` (a different card from a different game, Disney Lorcana) and unrelated `"Dragon Shield"` accessories. Used as the regression fixture for the `search-term-relevance` capability — see below. |

## Source form values

The values below are copied from the **currently configured** `Source` row for key `wt` in this app's database (verified 2026-08-17) — this is what the "Add/Edit Source" form should contain, not a re-derivation:

| SourceForm field | Value |
|---|---|
| `key` | `wt` |
| `name` | `Wizard's Tower` |
| `parser_key` | `wtfilters` |
| `rate_limit_profile` | *(blank — see Rate-limit signals below)* |
| `http_method` | `POST` |
| `base_search_url` | `https://app-filters.wizardtower.com/api/search` |
| `request_headers` | *(see below)* |
| `request_body_template` | *(see below)* |
| `page_size` | *(unset — `per_page: 24` is baked into the body template above instead)* |
| `max_pages` | `1` |

`request_headers`:
```json
{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Content-Type": "application/json", "Accept": "application/json", "Origin": "https://store.wizardtower.com", "Referer": "https://store.wizardtower.com/search?q={term}"}
```

`request_body_template`:
```json
{"context": {"mode": "buy", "page": 1, "per_page": 24, "sort": "manual"}, "filters": [], "q": "{term}", "include_facets": false, "preview": false}
```

Note for whoever fills out the form by hand: `request_body_template`'s `{term}` is substituted as **plain text** (not URL-encoded) per `Source.build_request_body`, unlike `request_headers`' `{term}` which IS URL-encoded via `Source.build_request_headers`. The `Referer` header above deliberately keeps `{term}` so it gets encoded correctly at fetch time.

Note also that `base_search_url` here contains no `{term}` placeholder at all — the search term travels in the POST body's `q` field, not the URL. `Source.build_search_url` handles this: for POST sources it only `.format(term=...)`s the URL when `{term}` is actually present, otherwise it uses `base_search_url` unchanged.

## Pagination

`WtFiltersParser.next_page_body` (`tracking/parsers.py`) is currently a stub that always returns `None` — this parser does not yet support following additional pages, regardless of `Source.max_pages`. This lines up with the live `Source.max_pages` value of `1` (single page only). The investigation doc notes the query used here (`Lightning Bolt`) has 627 total results across 27 pages at `per_page=24`; raising `max_pages` for this vendor would require implementing body-based pagination (incrementing `context.page`) in `next_page_body` first.

## Term relevance filtering

`wt`'s search API does not do phrase-aware matching — it returns any row containing any of the query's words, in any order, anywhere in the title. `search_results_sample.json` (query `Lightning Bolt`) happens not to demonstrate this, because every real print of a two-word card name keeps both words adjacent; `search_results_fire_dragon.json` does demonstrate it, and is the fixture used to test for it.

`JSONSearchParser.add_result` (`tracking/parsers.py`) now rejects any row whose title does not contain the search term as a contiguous, case/whitespace-normalized phrase, so `WtFiltersParser` (and every other `JSONSearchParser` subclass) drops these off-term rows automatically. This runs independent of, and prior to, the optional per-`ItemSource` `title_include_patterns`/`title_exclude_patterns`. See the `search-term-relevance` capability (`openspec/changes/search-term-relevance-filter/`) for the full design rationale and live evidence.

If this fixture is refreshed, ideally keep (or re-capture) at least one query whose results include an off-term row, so this filtering behavior stays covered by a real vendor response rather than only synthetic test data.

## Product-line / category filtering

`item-category-relevance-filter` adds two further, independent checks at the same `add_result` choke point: `expected_product_line` (checked against `data.results[].category`, the vendor's broad game/product-line signal) and `expected_category` (checked against `subcategory`/`category`, the existing narrow set-level signal). `search_results_product_line_mismatch.json` is a small supplementary fixture (not a refresh of `search_results_sample.json`) covering both: a same-titled `"Lightning Bolt"` row from an unrelated product line (Disney Lorcana), and a same-product-line row from an unrelated set (Masters 25 vs. an expected Strixhaven). If `search_results_sample.json` itself is refreshed, ideally include at least one off-product-line row and one off-category row there too for regression coverage, mirroring this note.

## Rate-limit signals

**Checked 2026-08-17, based on a single live sample.** One live `POST` was made to `https://app-filters.wizardtower.com/api/search` using the exact request config documented in the "Source form values" table above (test query `Lightning Bolt`). This confirms the *absence* of a signal on one ordinary request; it does not rule out header-based limiting that only activates under sustained/bursty load, which a single sample can't exercise.

Result: `200 OK` in ~1.67s. Full response header set observed:

```
Server: nginx
Date: Mon, 17 Aug 2026 22:41:02 GMT
Content-Type: application/json
Transfer-Encoding: chunked
Connection: keep-alive
X-Powered-By: PHP/8.4.24, PleskLin
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Strict-Transport-Security: max-age=31536000; includeSubDomains
Access-Control-Allow-Origin: https://store.wizardtower.com
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
Access-Control-Max-Age: 86400
Vary: Origin
```

- **No `RateLimit`/`RateLimit-Limit`/`RateLimit-Remaining`/`RateLimit-Reset` headers observed** (`ietf` profile signal absent).
- **No `X-RateLimit-Limit`/`X-RateLimit-Remaining`/`X-RateLimit-Reset` headers observed** (`x-ratelimit` profile signal absent).
- Response body top-level keys were `data`, `meta`, `error` — **no `extensions` key at all**, so `extensions.cost.throttleStatus` is absent (`graphql_cost` profile signal absent). This is consistent with the endpoint being a bespoke PHP/Plesk-hosted filter-search backend (`X-Powered-By: PHP/8.4.24, PleskLin`), not a GraphQL service.

**Recommendation:** leave `rate_limit_profile` blank/`none` for this vendor — none of the three registered profiles (`ietf`, `x-ratelimit`, `graphql_cost`) have any supporting evidence, and the vendor exposes no rate-limit signal of any kind on this endpoint that this app could key off of. Fall back to fixed-delay pacing. If throttling is ever observed in practice (e.g. sporadic 429s), revisit — but as of this check, no header- or body-based rate-limit contract exists to configure against.

## Refresh

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path

api = 'https://app-filters.wizardtower.com/api/search'
headers = {
    'User-Agent': 'Mozilla/5.0 (compatible; django_scraper fixture refresh)',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Origin': 'https://store.wizardtower.com',
    'Referer': 'https://store.wizardtower.com/search?q=Lightning+Bolt',
}
body = {
    'context': {'mode': 'buy', 'page': 1, 'per_page': 24, 'sort': 'manual'},
    'filters': [],
    'q': 'Lightning Bolt',
    'include_facets': False,
    'preview': False,
}
data = requests.post(api, json=body, headers=headers, timeout=60).json()
path = Path('tracking/fixtures/html/wt/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'results:', len(data['data']['results']))
"
```

After refresh, run `python manage.py test tracking.tests.WTInvestigationTests`.

See [tracking/docs/wt_investigation.md](../../../docs/wt_investigation.md) for the full investigation writeup (rendering decision, data selector maps).
