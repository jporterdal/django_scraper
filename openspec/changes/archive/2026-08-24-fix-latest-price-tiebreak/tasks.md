## 1. Query fix

- [x] 1.1 In `tracking/views.py`, `SearchableListView.get_queryset()`, add `price` and `title` as secondary and tertiary sort keys to the `source_latest` subquery: `.order_by("-update__timestamp", "price", "title")`.

## 2. Regression coverage

- [x] 2.1 Add a test (in `tracking/tests/test_sparkline.py` or `tracking/tests/test_scrape.py`, matching existing patterns in that file) covering a source whose single latest `WebUpdate` stores multiple in-stock `SearchResult` rows at different prices for the same item, asserting the cheapest of those tied rows is selected as that source's latest price.
- [x] 2.2 Within that test, insert the tied rows in a non-price-sorted order (e.g. most expensive first) to prove the fix isn't accidentally relying on insertion order.
- [x] 2.3 Add a test covering rows tied on both timestamp and price with different titles, asserting the alphabetically-first title is selected as the winning result regardless of insertion order.
- [x] 2.4 Run the full test suite (`python manage.py test`) and confirm existing `item-list-latest-price` coverage still passes unchanged.
