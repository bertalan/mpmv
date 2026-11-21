"""THM.31: `BrandPalette` rows, their seed migration and validation."""

import pytest
from django.core.exceptions import ValidationError

from apps.themes.models import BrandPalette
from apps.themes.palettes import PRESETS, ROLES
from apps.themes.services import registered_surfaces

pytestmark = pytest.mark.django_db


def test_the_data_migration_seeds_the_three_presets_with_the_spec_values() -> None:
    assert set(BrandPalette.objects.values_list("slug", flat=True)) >= set(PRESETS)
    for slug, values in PRESETS.items():
        row = BrandPalette.objects.get(slug=slug)
        assert row.name == values["name"]
        assert row.roles() == {role: values[role] for role in ROLES}


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_every_seeded_row_validates_against_the_registered_themes(slug: str) -> None:
    BrandPalette.objects.get(slug=slug).full_clean()


def test_registered_surfaces_come_from_the_resolved_theme_tokens() -> None:
    surfaces = registered_surfaces()
    assert surfaces
    assert all(s.startswith("#") for s in surfaces)


def test_a_failing_palette_is_refused_and_the_pair_is_named() -> None:
    row = BrandPalette.objects.get(slug="red")
    row.pk = None
    row.slug = "pale"
    row.primary = "#FF6B6B"
    with pytest.raises(ValidationError) as caught:
        row.full_clean()
    assert "on_primary on primary" in str(caught.value)


def test_a_colour_that_is_not_hex_is_refused() -> None:
    row = BrandPalette.objects.get(slug="red")
    row.primary = "red;}"
    with pytest.raises(ValidationError) as caught:
        row.full_clean()
    assert "primary" in caught.value.message_dict
