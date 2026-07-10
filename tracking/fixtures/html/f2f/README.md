# Face to Face Games HTML/JSON fixtures

Captured **2026-07-07** for Phase 2 Step 1 investigation.

## Files

| File | Purpose |
|------|---------|
| `search_results_sample.json` | **Primary fixture** — live response from `/apps/prod-indexer/search/...` for query `Lightning Bolt` (24 products, 52 variants on page) |
| `search_results_sample.html` | Synthetic static fragment documenting rendered card markup (not used for parsing) |
| `search_page_shell_stripped.html` | Search page HTML with `<script>` and `<style>` removed (reference only) |

## Refresh instructions

```bash
source venv/bin/activate
python -c "
import json, requests
from pathlib import Path
from urllib.parse import quote_plus

term = 'Lightning Bolt'
url = (
    'https://facetofacegames.com/apps/prod-indexer/search'
    f'/pageSize/24/page/1/keyword/{quote_plus(term)}'
)
headers = {
    'User-Agent': 'Mozilla/5.0 (compatible; django_scraper fixture refresh)',
    'Accept': 'application/json',
}
data = requests.get(url, headers=headers, timeout=30).json()
path = Path('tracking/fixtures/html/f2f/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'hits:', len(data['hits']['hits']))
"
```

After refresh, run `python manage.py test tracking.tests.F2FInvestigationTests`.

See [tracking/docs/f2f_investigation.md](../../docs/f2f_investigation.md) for full investigation notes.
