"""Seed the Red (default), Blue and Petrol palettes of spec 14 §14.12 (THM.31).

The values are frozen here: a later change to the presets is a new migration."""

from django.db import migrations

PRESETS = {
    "red": {
        "name": "Red",
        "primary": "#C40417",
        "primary_hover": "#9E0312",
        "primary_tint": "#FDECEE",
        "on_primary": "#FFFFFF",
        "primary_dark": "#FF6B6B",
        "secondary": "#2B3138",
        "secondary_hover": "#1B2026",
        "secondary_text": "#2B3138",
        "on_secondary": "#FFFFFF",
        "band": "#30373F",
        "text_on_band": "#FFFFFF",
        "focus": "#2F80ED",
        "control_border": "#8A8A8A",
    },
    "blue": {
        "name": "Blue",
        "primary": "#046BD2",
        "primary_hover": "#035AB1",
        "primary_tint": "#EEF6FF",
        "on_primary": "#FFFFFF",
        "primary_dark": "#0580FA",
        "secondary": "#FBA926",
        "secondary_hover": "#E89A1E",
        "secondary_text": "#BE5900",
        "on_secondary": "#1A1A1A",
        "band": "#30373F",
        "text_on_band": "#FFFFFF",
        "focus": "#2F80ED",
        "control_border": "#8A8A8A",
    },
    "petrol": {
        "name": "Petrol",
        "primary": "#0B5C7A",
        "primary_hover": "#08465D",
        "primary_tint": "#E6F2F6",
        "on_primary": "#FFFFFF",
        "primary_dark": "#7CC8E3",
        "secondary": "#C2410C",
        "secondary_hover": "#9A3412",
        "secondary_text": "#C2410C",
        "on_secondary": "#FFFFFF",
        "band": "#30373F",
        "text_on_band": "#FFFFFF",
        "focus": "#2F80ED",
        "control_border": "#8A8A8A",
    },
}



def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    palette = apps.get_model("themes", "BrandPalette")
    for slug, values in PRESETS.items():
        palette.objects.update_or_create(slug=slug, defaults=values)


def unseed(apps, schema_editor):  # type: ignore[no-untyped-def]
    apps.get_model("themes", "BrandPalette").objects.filter(slug__in=list(PRESETS)).delete()


class Migration(migrations.Migration):
    dependencies = [("themes", "0003_brand_palette")]

    operations = [migrations.RunPython(seed, unseed)]
