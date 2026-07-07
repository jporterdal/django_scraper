from django.db import migrations, models


def backfill_search_term(apps, schema_editor):
    SearchResult = apps.get_model("tracking", "SearchResult")
    for sr in SearchResult.objects.select_related("item").iterator():
        sr.search_term = sr.item.text
        sr.save(update_fields=["search_term"])


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0003_tag"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchresult",
            name="search_term",
            field=models.CharField(
                blank=True,
                default="",
                max_length=125,
                verbose_name="Search term used for this fetch",
            ),
        ),
        migrations.RunPython(backfill_search_term, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="searchresult",
            name="search_term",
            field=models.CharField(
                max_length=125,
                verbose_name="Search term used for this fetch",
            ),
        ),
    ]
