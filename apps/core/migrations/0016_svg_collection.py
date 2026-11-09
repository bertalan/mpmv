"""THM.12: mark the "Logos and icons" collection as the one that accepts SVG, by key.

The collection was created by 0015 and matched by name; a name can be duplicated or changed
by editors, so the collection is now identified by an `SvgCollection` row.
"""

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

NAME = "Logos and icons"  # the name 0015 gave the collection


def mark_logos_collection(apps: Any, schema_editor: Any) -> None:
    Collection = apps.get_model("wagtailcore", "Collection")
    SvgCollection = apps.get_model("core", "SvgCollection")
    logos = Collection.objects.filter(depth=2, name=NAME).order_by("path").first()
    if logos is not None:
        SvgCollection.objects.get_or_create(collection=logos)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_logos_collection"),
        ("wagtailcore", "0098_apitoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="SvgCollection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "collection",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="svg_policy",
                        to="wagtailcore.collection",
                    ),
                ),
            ],
            options={"verbose_name": "SVG collection"},
        ),
        migrations.RunPython(mark_logos_collection, migrations.RunPython.noop),
    ]
