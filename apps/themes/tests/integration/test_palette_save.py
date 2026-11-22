"""THM.33: the palette save service refuses a failing palette whatever the client sent."""

from pathlib import Path

import pytest
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.themes.engine import derive_palette
from apps.themes.models import BrandPalette
from apps.themes.palettes import PRESETS, PaletteRejected
from apps.themes.services import PALETTE_CONFIGURED, save_palette
from apps.themes.tests.builders import write_theme

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:theme-admin@example.com", source=AuditLog.Source.ADMIN)
BASE = {"primary": "#7A1FA2", "secondary": "#F2C94C", "band": "#222831"}


@pytest.fixture(autouse=True)
def themes(tmp_path: Path, settings: SettingsWrapper) -> None:
    root = tmp_path / "themes"
    write_theme(root, "base")
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"


def failing() -> dict[str, str]:
    roles = derive_palette(BASE)
    roles["on_primary"] = "#888888"
    return roles


def test_a_failing_palette_stores_nothing_and_names_the_pair_and_ratio() -> None:
    with pytest.raises(PaletteRejected) as raised:
        save_palette("mine", "Mine", failing(), ctx=CTX)
    pairs = {v.pair: v for v in raised.value.violations}
    violation = pairs["on_primary on primary"]
    assert violation.ratio is not None
    assert violation.ratio < 4.5
    assert "on_primary on primary" in str(raised.value)
    assert not BrandPalette.objects.filter(slug="mine").exists()
    assert not AuditLog.objects.filter(action=PALETTE_CONFIGURED).exists()


def test_a_palette_missing_a_role_is_refused() -> None:
    roles = derive_palette(BASE)
    del roles["focus"]
    with pytest.raises(PaletteRejected) as raised:
        save_palette("mine", "Mine", roles, ctx=CTX)
    assert raised.value.violations[0].kind == "missing"


def test_a_dry_run_returns_a_changeset_and_writes_nothing() -> None:
    changes = save_palette("mine", "Mine", derive_palette(BASE), ctx=CTX, dry_run=True)
    assert [c.op for c in changes.changes] == ["create"]
    assert not BrandPalette.objects.filter(slug="mine").exists()
    assert not AuditLog.objects.filter(action=PALETTE_CONFIGURED).exists()


def test_a_dry_run_of_a_failing_palette_is_refused_too() -> None:
    with pytest.raises(PaletteRejected):
        save_palette("mine", "Mine", failing(), ctx=CTX, dry_run=True)


def test_a_save_stores_the_palette_and_audits_old_and_new_values() -> None:
    roles = derive_palette(BASE)
    save_palette("mine", "Mine", roles, ctx=CTX)
    stored = BrandPalette.objects.get(slug="mine")
    assert stored.roles() == roles
    created = AuditLog.objects.get(action=PALETTE_CONFIGURED)
    assert created.after["primary"] == BASE["primary"]

    changed = derive_palette({**BASE, "primary": "#5B1580"})
    save_palette("mine", "Mine", changed, ctx=CTX)
    update = AuditLog.objects.filter(action=PALETTE_CONFIGURED).latest("pk")
    assert update.before["primary"] == BASE["primary"]
    assert update.after["primary"] == "#5B1580"


def test_a_predefined_palette_cannot_be_overwritten_by_the_builder() -> None:
    with pytest.raises(PaletteRejected):
        save_palette("red", "Red", derive_palette(BASE), ctx=CTX)
    assert BrandPalette.objects.get(slug="red").primary == PRESETS["red"]["primary"]
