import i18nfield.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pretix_tables", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="numberedroom",
            name=name,
            field=i18nfield.fields.I18nCharField(blank=True, max_length=100, null=True),
        )
        for name in ("term_space", "term_spaces", "term_table", "term_tables")
    ]
