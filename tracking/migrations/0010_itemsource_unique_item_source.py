from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tracking", "0007_source_page_size_source_parser_key_and_more"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="itemsource",
            unique_together={("item", "source")},
        ),
    ]
