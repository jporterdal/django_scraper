import re


def title_matches_rules(title: str, include_patterns: list, exclude_patterns: list) -> bool:
    """Return True if title passes include/exclude rules. Empty lists = pass."""
    if exclude_patterns and any(re.search(p, title, re.I) for p in exclude_patterns):
        return False
    if include_patterns and not any(re.search(p, title, re.I) for p in include_patterns):
        return False
    return True


def result_matches_item_source(result_title, item_source) -> bool:
    """Return True if a result title passes an ItemSource's include/exclude patterns."""
    return title_matches_rules(
        result_title,
        item_source.title_include_patterns or [],
        item_source.title_exclude_patterns or [],
    )
