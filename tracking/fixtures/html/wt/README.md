# Wizard's Tower HTML/JSON fixtures

Captured **2026-07-07** for vendor search investigation.

## Files

| File | Purpose |
|------|---------|
| `search_results_sample.json` | **Primary fixture** — wt-filters `POST /api/search` response for `Lightning Bolt` (24 results, page 1) |
| `search_results_sample.html` | Synthetic static fragment documenting product card fields |
| `search_page_shell_stripped.html` | Search page HTML with `<script>` and `<style>` removed (reference only) |

## Refresh instructions

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
    'include_facets': True,
    'preview': False,
}
data = requests.post(api, json=body, headers=headers, timeout=60).json()
path = Path('tracking/fixtures/html/wt/search_results_sample.json')
path.write_text(json.dumps(data, indent=2))
print('Wrote', path, 'results:', len(data['data']['results']))
"
```

After refresh, run `python manage.py test tracking.tests.WTInvestigationTests`.

See [tracking/docs/wt_investigation.md](../../docs/wt_investigation.md) for full investigation notes.
