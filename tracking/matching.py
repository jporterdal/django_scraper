import re
import unicodedata


def _normalize_for_match(value):
    """Lowercase, fold diacritics, and collapse whitespace, for term/title comparison.

    Diacritic folding only covers characters that decompose into a base letter
    plus a combining mark under NFKD (e.g. i + acute accent). Characters that
    are their own distinct letter rather than an accented variant of one -
    e.g. German ß, æ, ø - do not decompose this way and are intentionally left
    as exact-match only rather than growing this into a transliteration engine.
    """
    text = unicodedata.normalize("NFKD", str(value).strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def term_matches(title, term) -> bool:
    """True if normalized ``term`` appears as a contiguous phrase in normalized ``title``.

    A blank/empty term always matches (the check is disabled).
    """
    term_normalized = _normalize_for_match(term)
    if not term_normalized:
        return True
    return term_normalized in _normalize_for_match(title)


def value_matches_any(value, expected_values) -> bool:
    """True if normalized ``value`` contains any normalized ``expected_values`` entry.

    Each expected value is ``re.escape()``'d before matching so callers
    (item-level ``expected_product_line``/``expected_category`` values) can
    contain regex metacharacters without any special-character surprises.
    An empty ``expected_values`` list disables the check (always passes).
    """
    if not expected_values:
        return True
    normalized_value = _normalize_for_match(value)
    return any(
        re.search(re.escape(_normalize_for_match(expected)), normalized_value)
        for expected in expected_values
    )


def title_matches_rules(title: str, include_patterns: list, exclude_patterns: list) -> bool:
    """Return True if title passes include/exclude rules. Empty lists = pass."""
    if exclude_patterns and any(re.search(p, title, re.I) for p in exclude_patterns):
        return False
    if include_patterns and not any(re.search(p, title, re.I) for p in include_patterns):
        return False
    return True


def result_matches_item_source(title, category, product_line, item_source) -> bool:
    """Return True if a result currently matches its ItemSource's/SearchableItem's criteria.

    Runs all four relevance checks (search term, expected product line,
    expected category, title include/exclude patterns) and returns a single
    pass/fail — used by read-time re-validation (list view, detail chart).
    Ingest (``parsers.JSONSearchParser.add_result``) calls the individual
    checks directly instead, to keep per-check rejection logging.
    """
    item = item_source.item
    source_key = item_source.source_id

    if not term_matches(title, item.text):
        return False
    if not value_matches_any(
        product_line, item.expected_values_for_source("product_line", source_key)
    ):
        return False
    if not value_matches_any(
        category, item.expected_values_for_source("category", source_key)
    ):
        return False
    return title_matches_rules(
        title,
        item_source.title_include_patterns or [],
        item_source.title_exclude_patterns or [],
    )
