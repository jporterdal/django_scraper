"""Seed-data protection (design D10).

Seed rows are identified by the fixed PKs in ``seed.json``: items and tags
directly, item-sources and item metadata through their item. View-level
checks give visitors a friendly message; the signal receivers below are a
backstop that refuses any ORM write to seed rows outside a reset.
"""

import threading
from contextlib import contextmanager

from django.db.models.signals import m2m_changed, pre_delete, pre_save

from . import dataset, is_demo

PROTECTED_MESSAGE = "This is a protected demo item and can't be changed. Try one of your own items instead."

_state = threading.local()


class ProtectedDemoDataError(Exception):
    """A write targeted protected seed data outside a demo reset."""


@contextmanager
def allow_seed_writes():
    """Permit writes to seed rows for the duration of the block (used by resets)."""
    depth = getattr(_state, "depth", 0)
    _state.depth = depth + 1
    try:
        yield
    finally:
        _state.depth = depth


def seed_writes_allowed():
    return getattr(_state, "depth", 0) > 0


def is_protected(obj):
    """True when ``obj`` is (or belongs to) seed data. Always False outside demo mode."""
    from ..models import ItemMetadata, ItemSource, SearchableItem, Tag

    if not is_demo() or obj is None:
        return False
    if isinstance(obj, SearchableItem):
        return obj.pk in dataset.seed_item_pks()
    if isinstance(obj, Tag):
        return obj.pk in dataset.seed_tag_pks()
    if isinstance(obj, (ItemSource, ItemMetadata)):
        return obj.item_id in dataset.seed_item_pks()
    return False


def visitor_items():
    from ..models import SearchableItem

    return SearchableItem.objects.exclude(pk__in=dataset.seed_item_pks())


def visitor_tags():
    from ..models import Tag

    return Tag.objects.exclude(pk__in=dataset.seed_tag_pks())


def _guard(instance):
    if seed_writes_allowed() or not is_demo():
        return
    if is_protected(instance):
        raise ProtectedDemoDataError(f"{instance!r} is protected demo data")


def _on_pre_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    _guard(instance)


def _on_pre_delete(sender, instance, **kwargs):
    _guard(instance)


def _on_tags_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if seed_writes_allowed() or not is_demo():
        return
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if not reverse:
        # item.tags.add/remove/clear: refuse if the item is seed data.
        _guard(instance)
        return
    # tag.items.add/remove/clear: refuse if any affected item is seed data.
    seed_items = dataset.seed_item_pks()
    if pk_set is None:
        if instance.items.filter(pk__in=seed_items).exists():
            raise ProtectedDemoDataError("Change would alter protected demo items' tags")
    elif set(pk_set) & seed_items:
        raise ProtectedDemoDataError("Change would alter protected demo items' tags")


def connect_signals():
    from ..models import ItemMetadata, ItemSource, SearchableItem, Tag

    for model in (SearchableItem, ItemSource, ItemMetadata, Tag):
        pre_save.connect(_on_pre_save, sender=model, dispatch_uid=f"demo_protect_save_{model.__name__}")
        pre_delete.connect(_on_pre_delete, sender=model, dispatch_uid=f"demo_protect_delete_{model.__name__}")
    m2m_changed.connect(
        _on_tags_changed,
        sender=SearchableItem.tags.through,
        dispatch_uid="demo_protect_item_tags",
    )
