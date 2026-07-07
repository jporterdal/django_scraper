from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0002_source_base_search_url"),
    ]

    operations = [
        migrations.CreateModel(
            name="Tag",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(max_length=50, unique=True, verbose_name="Tag name"),
                ),
                (
                    "color",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=7,
                        verbose_name="Optional badge color (hex, e.g. #3498db)",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="searchableitem",
            name="tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="items",
                to="tracking.tag",
                verbose_name="Tags for grouping and filtering items",
            ),
        ),
    ]
