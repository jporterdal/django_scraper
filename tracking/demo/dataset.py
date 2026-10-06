"""Loader for the bundled demo dataset in ``tracking/demo/data/`` (design D6).

Every file is plain JSON, loaded once per process and validated on load so a
malformed dataset fails loudly at the first demo request rather than
producing a half-seeded sandbox.
"""

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

DEMO_URL_SCHEME = "demo"


class DemoDataError(Exception):
    """The bundled demo dataset is missing a file or has the wrong shape."""


def _load(relative_path):
    path = DATA_DIR / relative_path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoDataError(f"Missing demo data file: {relative_path}") from exc
    except json.JSONDecodeError as exc:
        raise DemoDataError(f"Invalid JSON in demo data file {relative_path}: {exc}") from exc


def _require(obj, keys, where):
    if not isinstance(obj, dict):
        raise DemoDataError(f"{where}: expected an object, got {type(obj).__name__}")
    missing = [key for key in keys if key not in obj]
    if missing:
        raise DemoDataError(f"{where}: missing key(s) {', '.join(missing)}")


def _require_list(obj, where):
    if not isinstance(obj, list):
        raise DemoDataError(f"{where}: expected a list, got {type(obj).__name__}")


def demo_search_url_template(source_key):
    return f"{DEMO_URL_SCHEME}://{source_key}/search?q={{term}}"


@lru_cache(maxsize=None)
def sources():
    """Preset demo Sources, in display order."""
    data = _load("sources.json")
    _require_list(data, "sources.json")
    for index, source in enumerate(data):
        _require(source, ["key", "name", "parser_key"], f"sources.json[{index}]")
    return tuple(data)


def sources_by_key():
    return {source["key"]: source for source in sources()}


@lru_cache(maxsize=None)
def catalogue(source_key):
    """Neutral-form products one demo Source can return (design D6)."""
    if source_key not in sources_by_key():
        raise DemoDataError(f"No demo source with key {source_key!r}")
    data = _load(f"catalogue/{source_key}.json")
    _require_list(data, f"catalogue/{source_key}.json")
    for index, product in enumerate(data):
        where = f"catalogue/{source_key}.json[{index}]"
        _require(product, ["id", "title", "category", "product_line", "variants"], where)
        _require_list(product["variants"], f"{where}.variants")
        if not product["variants"]:
            raise DemoDataError(f"{where}: needs at least one variant")
        for v_index, variant in enumerate(product["variants"]):
            _require(variant, ["condition", "price", "instock"], f"{where}.variants[{v_index}]")
    return tuple(data)


@lru_cache(maxsize=None)
def seed():
    """The seed manifest: protected tags/items with fixed PKs, plus schedules."""
    data = _load("seed.json")
    _require(data, ["user", "tags", "items", "schedules", "try_terms"], "seed.json")
    _require(data["user"], ["username"], "seed.json.user")
    source_keys = set(sources_by_key())
    tag_pks = set()
    for index, tag in enumerate(data["tags"]):
        _require(tag, ["pk", "name", "color"], f"seed.json.tags[{index}]")
        tag_pks.add(tag["pk"])
    preset = set(preset_pattern_values())
    entry_ids = {entry["id"] for entry in metadata_entries()}
    for index, item in enumerate(data["items"]):
        where = f"seed.json.items[{index}]"
        _require(
            item,
            [
                "pk", "text", "priority", "tags", "metadata_external_id",
                "expected_product_line", "expected_category", "sources",
            ],
            where,
        )
        unknown_tags = set(item["tags"]) - tag_pks
        if unknown_tags:
            raise DemoDataError(f"{where}: unknown tag pk(s) {sorted(unknown_tags)}")
        if item["metadata_external_id"] and item["metadata_external_id"] not in entry_ids:
            raise DemoDataError(f"{where}: unknown metadata id {item['metadata_external_id']!r}")
        for s_index, item_source in enumerate(item["sources"]):
            s_where = f"{where}.sources[{s_index}]"
            _require(item_source, ["source", "include", "exclude"], s_where)
            if item_source["source"] not in source_keys:
                raise DemoDataError(f"{s_where}: unknown source {item_source['source']!r}")
            bad = set(item_source["include"]) | set(item_source["exclude"])
            bad -= preset
            if bad:
                raise DemoDataError(f"{s_where}: non-preset pattern(s) {sorted(bad)}")
    for index, schedule in enumerate(data["schedules"]):
        _require(
            schedule,
            ["name", "frequency", "anchor_time", "tag", "enabled"],
            f"seed.json.schedules[{index}]",
        )
    return data


def seed_item_pks():
    return frozenset(item["pk"] for item in seed()["items"])


def seed_tag_pks():
    return frozenset(tag["pk"] for tag in seed()["tags"])


@lru_cache(maxsize=None)
def metadata_entries():
    """Bundled metadata entries served by the demo metadata provider."""
    data = _load("metadata.json")
    _require_list(data, "metadata.json")
    for index, entry in enumerate(data):
        _require(
            entry,
            ["id", "name", "type_line", "text", "set", "color", "motif", "thumbnail"],
            f"metadata.json[{index}]",
        )
    return tuple(data)


@lru_cache(maxsize=None)
def preset_patterns():
    """``(pattern, label)`` pairs: the only title patterns accepted in demo mode."""
    data = _load("patterns.json")
    _require_list(data, "patterns.json")
    for index, entry in enumerate(data):
        _require(entry, ["pattern", "label"], f"patterns.json[{index}]")
    return tuple((entry["pattern"], entry["label"]) for entry in data)


def preset_pattern_values():
    return tuple(pattern for pattern, _label in preset_patterns())
