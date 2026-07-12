"""Hand-rolled test object factories (not collected as tests)."""

from django.contrib.auth.models import User

from tracking.models import (
    CC_DEFAULT_SEARCH_URL,
    ItemSource,
    SearchableItem,
    SearchResult,
    Source,
    WebUpdate,
)


def make_user(username="testuser", *, password="testpass", **extra):
    user, _ = User.objects.get_or_create(username=username, defaults=extra)
    user.set_password(password)
    user.save()
    return user


def make_source(
    key="cc",
    *,
    name="Test Source",
    parser_key="cc",
    base_search_url="https://example.com/search?s={term}",
    **extra,
):
    defaults = {
        "name": name,
        "parser_key": parser_key,
        "base_search_url": base_search_url,
        **extra,
    }
    source, _ = Source.objects.update_or_create(key=key, defaults=defaults)
    return source


def make_cc_source(**extra):
    """Source using the migration-seeded ``cc`` key and default CC search URL."""
    return make_source(
        key="cc",
        name=extra.pop("name", "Canada Computers"),
        parser_key=extra.pop("parser_key", "cc"),
        base_search_url=extra.pop("base_search_url", CC_DEFAULT_SEARCH_URL),
        **extra,
    )


def make_item(text="test item", *, active=True, **extra):
    return SearchableItem.objects.create(text=text, active=active, **extra)


def make_item_source(item, source, **extra):
    return ItemSource.objects.create(item=item, source=source, **extra)


def make_linked_item(*, source=None, item_text="test item", active=True, **item_source_extra):
    if source is None:
        source = make_source()
    item = make_item(text=item_text, active=active)
    item_source = make_item_source(item, source, **item_source_extra)
    return source, item, item_source


def make_web_update(**extra):
    return WebUpdate.objects.create(**extra)


def make_search_result(item, source, update, **extra):
    defaults = {
        "title": "Test Product",
        "search_term": item.text,
        "price": 19.99,
        "category": "Hardware",
        "instock": 1,
        "item": item,
        "source": source,
        "update": update,
    }
    defaults.update(extra)
    return SearchResult.objects.create(**defaults)
