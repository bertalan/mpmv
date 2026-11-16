"""SHP.15: the Ionian, Sporades, Saronic and Northern Ireland special areas."""

from importlib import import_module
from typing import Any

import pytest
from django.apps import apps as global_apps

from apps.shipping import engine
from apps.shipping.engine import Destination
from apps.shipping.models import SpecialArea
from apps.shipping.services import special_area_snaps

pytestmark = pytest.mark.django_db

migration: Any = import_module("apps.shipping.migrations.0014_seed_special_areas_complete")
NEW_CODES = {code for code, *_ in migration.SEED}


@pytest.mark.parametrize(
    ("dest", "area"),
    [
        (Destination("GR", "49100"), "gr-ionian-islands"),  # Corfu
        (Destination("GR", "281 00"), "gr-ionian-islands"),  # Argostoli
        (Destination("GR", "29100"), "gr-ionian-islands"),  # Zakynthos
        (Destination("GR", "31100"), "gr-ionian-islands"),  # Lefkada
        (Destination("GR", "37002"), "gr-sporades"),  # Skiathos
        (Destination("GR", "37003"), "gr-sporades"),  # Skopelos
        (Destination("GR", "18010"), "gr-saronic-islands"),  # Aegina
        (Destination("GR", "18020"), "gr-saronic-islands"),  # Poros
        (Destination("GR", "18040"), "gr-saronic-islands"),  # Hydra
        (Destination("GR", "18050"), "gr-saronic-islands"),  # Spetses
        (Destination("GR", "34007"), "gr-sporades"),  # Skyros
        (Destination("GR", "80100"), "gr-ionian-islands"),  # Kythira
        (Destination("GR", "18900"), "gr-saronic-islands"),  # Salamina
        (Destination("GB", "BT1 1AA"), "gb-northern-ireland"),
        (Destination("GB", "bt7 3ab"), "gb-northern-ireland"),
    ],
)
def test_new_areas_match_their_addresses(dest: Destination, area: str) -> None:
    found = engine.resolve_special_area(special_area_snaps(), dest)
    assert found is not None
    assert found.code == area


@pytest.mark.parametrize(
    "dest",
    [
        Destination("GR", "26221"),  # Patras
        Destination("GR", "10431"),  # Athens
        Destination("GR", "18510"),  # Piraeus
        Destination("GR", "18648"),  # Piraeus area
        Destination("GR", "18030"),  # Methana peninsula, mainland (owner answer)
        Destination("GR", "34100"),  # Chalkida (next to Skyros 34007)
        Destination("GR", "27100"),  # Pyrgos
        Destination("GR", "30100"),  # Agrinio
        Destination("GR", "48100"),  # Preveza
        Destination("GR", "38221"),  # Volos
        Destination("GB", "SW1A 1AA"),  # London
        Destination("GB", "G1 1AA"),  # Glasgow
        Destination("GB", "B1 1AA"),  # Birmingham, starts with B
        Destination("IE", "BT1 1AA"),  # the pattern is for GB only
    ],
)
def test_nearby_mainland_codes_stay_mainland(dest: Destination) -> None:
    assert engine.resolve_special_area(special_area_snaps(), dest) is None


def test_new_areas_are_valid_and_not_extra_vat() -> None:
    for area in SpecialArea.objects.filter(code__in=NEW_CODES):
        area.full_clean()
        assert area.kind != "extra_vat"
        assert area.territory_code == ""  # tax territories are untouched (TAX.7)
    assert SpecialArea.objects.filter(code__in=NEW_CODES).count() == len(NEW_CODES)


def test_the_seed_is_idempotent_and_keeps_staff_edits() -> None:
    SpecialArea.objects.filter(code="gr-sporades").update(postcode_patterns=["370*", "34007"])
    before = SpecialArea.objects.count()
    migration.seed(global_apps, None)
    migration.seed(global_apps, None)
    assert SpecialArea.objects.count() == before
    assert SpecialArea.objects.get(code="gr-sporades").postcode_patterns == ["370*", "34007"]


def test_the_reverse_removes_only_what_the_seed_added() -> None:
    keep = SpecialArea.objects.create(
        code="xx-custom", name="Custom", kind="remote", countries=["GR"], postcode_patterns=["99*"]
    )
    migration.unseed(global_apps, None)
    assert not SpecialArea.objects.filter(code__in=NEW_CODES).exists()
    assert SpecialArea.objects.filter(pk=keep.pk).exists()
    assert SpecialArea.objects.filter(code="gr-crete").count() == 1
    migration.seed(global_apps, None)  # and it can be seeded again
    assert SpecialArea.objects.filter(code__in=NEW_CODES).count() == len(NEW_CODES)
