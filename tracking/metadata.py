"""Shared metadata-refresh entrypoint (see item-metadata-enrichment capability).

``request_metadata_refresh`` is the *only* code path allowed to enqueue a
``MetadataFetchRequest``; every create/update/manual-retry path funnels
through it (directly, or via ``sync_metadata_after_save`` below) so the
staleness-trigger logic lives in one place instead of being reimplemented per
view.
"""

from .models import ItemMetadata, MetadataFetchRequest


def request_metadata_refresh(item):
    """(Re-)request a metadata fetch for ``item``.

    Creates ``ItemMetadata`` if absent, then enqueues a ``MetadataFetchRequest``.
    Dispatch happens asynchronously, drained at a bounded rate by
    ``tracking.tasks.drain_metadata_fetch_queue``.
    """
    ItemMetadata.objects.get_or_create(item=item)
    MetadataFetchRequest.objects.create(item=item)


def reset_item_metadata(item):
    """Clear fetched state back to ``unfetched`` (provider changed or cleared)."""
    ItemMetadata.objects.update_or_create(
        item=item,
        defaults={
            "status": ItemMetadata.Status.UNFETCHED,
            "external_id": "",
            "pinned_external_id": "",
            "payload": {},
        },
    )


def get_item_metadata(item):
    """Return ``item``'s ``ItemMetadata`` if it exists, else ``None``.

    Safe to call on an item loaded via ``select_related("metadata")`` — the
    reverse one-to-one accessor raises ``DoesNotExist`` rather than returning
    ``None`` when no row exists.
    """
    try:
        return item.metadata
    except ItemMetadata.DoesNotExist:
        return None


def sync_metadata_after_save(item, *, provider_changed, text_changed):
    """Apply the staleness-trigger/reset contract after a create or update save.

    ``provider_changed``: ``metadata_provider_key`` differs from what it was
    before this save (including blank <-> non-blank transitions; for a brand
    new item, "before" is the blank default).
    ``text_changed``: ``text`` differs from what it was before this save.

    Trigger conditions (mirrors the design doc):
    - provider changed (including cleared): reset fetched state, then
      request a refresh only if the new value is non-blank.
    - provider unchanged and non-blank: request a refresh when ``text``
      changed and the item has no ``pinned_external_id`` (a pinned match is
      an explicit override, immune to text-change invalidation).
    """
    if provider_changed:
        reset_item_metadata(item)
        if item.metadata_provider_key:
            request_metadata_refresh(item)
        return

    if not item.metadata_provider_key:
        return

    if text_changed:
        item_metadata = get_item_metadata(item)
        pinned = item_metadata.pinned_external_id if item_metadata else ""
        if not pinned:
            request_metadata_refresh(item)
