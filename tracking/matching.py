import re


def title_matches_rules(title: str, include_patterns: list, exclude_patterns: list) -> bool:
    """Return True if title passes include/exclude rules. Empty lists = pass."""
    if exclude_patterns and any(re.search(p, title, re.I) for p in exclude_patterns):
        return False
    if include_patterns and not any(re.search(p, title, re.I) for p in include_patterns):
        return False
    return True
