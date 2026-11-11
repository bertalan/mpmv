"""VEN.27: the jurisdictions that issue no TIN become staff-editable, effective-dated data,
seeded with the six countries that were the `engine.NO_TIN_COUNTRIES` constant (owner decision
MQ-16). A row is valid over `[valid_from, valid_to)`: a removal ends it, it is never deleted."""

import datetime
import uuid

import django.contrib.postgres.constraints
from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations, models

import apps.core.fields
import apps.vendors.models

SEED = ("AE", "BM", "BS", "KY", "MC", "VG")
SEED_REASON = "Seeded from the list that was a code constant; the accountant still confirms it."
SEED_FROM = datetime.date(2023, 1, 1)  # DAC7 applies from 2023: the list holds for every period


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    NoTinCountry = apps.get_model("vendors", "NoTinCountry")
    for country in SEED:
        NoTinCountry.objects.get_or_create(
            country=country, defaults={"reason": SEED_REASON, "valid_from": SEED_FROM}
        )


class Migration(migrations.Migration):
    dependencies = [("vendors", "0026_storefront_rich_text")]

    operations = [
        BtreeGistExtension(),
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
                (
                    "country",
                    apps.core.fields.CountryField(
                        help_text=(
                            "The country whose authorities issue no tax identification number "
                            "(TIN)."
                        ),
                        max_length=2,
                    ),
                ),
                (
                    "reason",
                    models.CharField(help_text="Why this country issues no TIN.", max_length=300),
                ),
                (
                    "valid_from",
                    models.DateField(
                        help_text=(
                            "First day (business time zone) on which the country counts as "
                            "issuing no TIN."
                        )
                    ),
                ),
                (
                    "valid_to",
                    models.DateField(
                        blank=True,
                        help_text=(
                            "Day on which it stops counting (not included); empty while it "
                            "still counts."
                        ),
                        null=True,
                    ),
                ),
                (
                    "removal_reason",
                    models.CharField(
                        blank=True,
                        help_text="Why staff removed the country; empty while listed.",
                        max_length=300,
                    ),
                ),
            ],
            options={
                "verbose_name": "no-TIN country",
                "verbose_name_plural": "no-TIN countries",
                "ordering": ["country", "-valid_from"],
            },
        ),
        migrations.AddConstraint(
            model_name="notincountry",
            constraint=models.CheckConstraint(
                condition=models.Q(("valid_to__isnull", True))
                | models.Q(("valid_to__gte", models.F("valid_from"))),
                name="vendors_notincountry_valid_period",
            ),
        ),
        migrations.AddConstraint(
            model_name="notincountry",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                expressions=[
                    ("country", "="),
                    (apps.vendors.models.DateRangeExpr("valid_from", "valid_to"), "&&"),
                ],
                name="vendors_notincountry_no_overlap",
                violation_error_message="This country is already on the list for those days.",
            ),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
