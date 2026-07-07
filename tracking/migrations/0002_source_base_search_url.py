from django.db import migrations, models

CC_DEFAULT_SEARCH_URL = (
    "https://www.canadacomputers.com/en/search?s={term}&pickup=62"
)


def set_canada_computers_url(apps, schema_editor):
    Source = apps.get_model("tracking", "Source")
    Source.objects.filter(key="cc").update(base_search_url=CC_DEFAULT_SEARCH_URL)
    if not Source.objects.filter(key="cc").exists():
        Source.objects.create(
            key="cc",
            name="Canada Computers",
            base_search_url=CC_DEFAULT_SEARCH_URL,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="source",
            name="base_search_url",
            field=models.CharField(
                default=CC_DEFAULT_SEARCH_URL,
                max_length=500,
                verbose_name="Search URL template; use {term} for the URL-encoded query string",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="itemsource",
            name="url_suffix",
            field=models.CharField(
                blank=True,
                default="",
                max_length=250,
                verbose_name="Optional extra query string appended to the source search URL",
            ),
        ),
        migrations.RunPython(set_canada_computers_url, migrations.RunPython.noop),
    ]
