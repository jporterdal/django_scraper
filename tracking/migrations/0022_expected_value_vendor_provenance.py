from django.db import migrations, models


FIELD_MAP = {
    "expected_product_line": "product_line",
    "expected_category": "category",
}


def migrate_expected_values_to_vendor_provenance(apps, schema_editor):
    """Best-effort single-vendor attribution per item/value (design.md Decision 6).

    Per stored string, checks the item's currently-configured vendors'
    ``ObservedCategoryValue`` rows for that field; tags the migrated entry
    with a vendor key only when exactly one configured vendor's rows match,
    else degrades to ``source: None`` (unattributed/universal).
    """
    SearchableItem = apps.get_model("tracking", "SearchableItem")
    ItemSource = apps.get_model("tracking", "ItemSource")
    ObservedCategoryValue = apps.get_model("tracking", "ObservedCategoryValue")

    for item in SearchableItem.objects.all():
        source_ids = list(
            ItemSource.objects.filter(item=item).values_list("source_id", flat=True)
        )
        update_fields = []
        for model_field, observed_field_name in FIELD_MAP.items():
            stored = getattr(item, model_field) or []
            if not stored or isinstance(stored[0], dict):
                continue
            migrated = []
            for value in stored:
                matching_keys = sorted(set(
                    ObservedCategoryValue.objects.filter(
                        source_id__in=source_ids,
                        field_name=observed_field_name,
                        value=value,
                    ).values_list("source__key", flat=True)
                ))
                source_key = matching_keys[0] if len(matching_keys) == 1 else None
                migrated.append({"value": value, "source": source_key})
            setattr(item, model_field, migrated)
            update_fields.append(model_field)
        if update_fields:
            item.save(update_fields=update_fields)


def reverse_expected_values_to_plain_strings(apps, schema_editor):
    """Lossless reverse: list[{"value", "source"}] -> list[str], dropping source."""
    SearchableItem = apps.get_model("tracking", "SearchableItem")

    for item in SearchableItem.objects.all():
        update_fields = []
        for model_field in FIELD_MAP:
            stored = getattr(item, model_field) or []
            if not stored or not isinstance(stored[0], dict):
                continue
            setattr(item, model_field, [entry["value"] for entry in stored])
            update_fields.append(model_field)
        if update_fields:
            item.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('tracking', '0021_item_metadata_enrichment'),
    ]

    operations = [
        migrations.RunPython(
            migrate_expected_values_to_vendor_provenance,
            reverse_expected_values_to_plain_strings,
        ),
        migrations.AlterField(
            model_name='searchableitem',
            name='expected_category',
            field=models.JSONField(blank=True, default=list, verbose_name="Expected category/set value(s), each a {'value': str, 'source': str|None} entry (e.g. a specific MTG set, possibly spelled differently per vendor) — a result from vendor V must contain at least one of V's applicable values (V's own tagged entries plus every source=None/manual entry) to narrow results beyond product-line disambiguation. Independent of expected product line. A vendor with no applicable entries has this check disabled for its own rows. See expected-value-vendor-provenance design.md."),
        ),
        migrations.AlterField(
            model_name='searchableitem',
            name='expected_product_line',
            field=models.JSONField(blank=True, default=list, verbose_name="Expected product line(s), each a {'value': str, 'source': str|None} entry (e.g. {'value': 'Magic', 'source': None}, or one entry per vendor when vendors word it differently) — a result from vendor V must contain at least one of V's applicable values (V's own tagged entries plus every source=None/manual entry) to disambiguate this item from a same-titled item in an unrelated product line. A vendor with no applicable entries has this check disabled for its own rows. See expected-value-vendor-provenance design.md."),
        ),
    ]
