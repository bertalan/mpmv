"""VEN.27: the jurisdictions that issue no TIN become staff-editable data, seeded with the six
countries that were the `engine.NO_TIN_COUNTRIES` constant (owner decision MQ-16)."""

import uuid

from django.db import migrations, models

import apps.core.fields

SEED = ("AE", "BM", "BS", "KY", "MC", "VG")
SEED_REASON = "Seeded from the list that was a code constant; the accountant still confirms it."


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    NoTinCountry = apps.get_model("vendors", "NoTinCountry")
    for country in SEED:
        NoTinCountry.objects.get_or_create(country=country, defaults={"reason": SEED_REASON})


class Migration(migrations.Migration):
    dependencies = [("vendors", "0026_storefront_rich_text")]

    operations = [
        migrations.CreateModel(
            name="NoTinCountry",
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
                ("country", apps.core.fields.CountryField(max_length=2, unique=True)),
                (
                    "reason",
                    models.CharField(help_text="Why this country issues no TIN.", max_length=300),
                ),
            ],
            options={
                "verbose_name": "no-TIN country",
                "verbose_name_plural": "no-TIN countries",
                "ordering": ["country"],
            },
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
