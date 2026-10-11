import django.db.models.deletion
from django.db import migrations, models

import pretix_tables.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("pretixbase", "0311_fix_unshredded_invoices"),
    ]

    operations = [
        migrations.CreateModel(
            name="NumberedRoom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("pairs", models.BooleanField(default=False)),
                ("map", models.FileField(blank=True, max_length=255, null=True,
                                         upload_to=pretix_tables.models.room_map_upload_to)),
                ("item", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="numbered_room", to="pretixbase.item")),
                ("quota", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name="+", to="pretixbase.quota")),
            ],
        ),
        migrations.CreateModel(
            name="NumberedTable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=190)),
                ("size", models.PositiveIntegerField()),
                ("position", models.IntegerField(default=0)),
                ("blocked", models.CharField(blank=True, default="", max_length=500)),
                ("room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tables",
                                           to="pretix_tables.numberedroom")),
            ],
            options={
                "ordering": ("position", "id"),
            },
        ),
    ]
