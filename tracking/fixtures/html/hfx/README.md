# HFX Games HTML/JSON fixtures

Captured **2026-07-07** for vendor search investigation.

## Files

| File | Purpose |
|------|---------|
| `search_results_sample.json` | **Primary fixture** — Storepass API response for `Lightning Bolt` (MTG product line, 30 products) |
| `search_results_sample.html` | Synthetic static fragment documenting Storepass card markup |
| `search_page_shell_stripped.html` | Search page HTML with `<script>` and `<style>` removed (reference only) |

## Refresh instructions

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

See [tracking/docs/hfx_investigation.md](../../docs/hfx_investigation.md) for full investigation notes.
