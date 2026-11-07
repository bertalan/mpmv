"""THM.1: system checks of the theme registry (spec 14 §14.3, Selection).

themes.E001 an invalid manifest or chain, themes.E002 an unknown `MPMV_THEME_DEFAULT`,
themes.E004 no `base` theme. A stored site setting naming an unknown theme (themes.E003) needs
the database: see the integration tests.
"""

from pathlib import Path

import pytest
from django.core import checks
from pytest_django.fixtures import SettingsWrapper

from apps.themes.checks import check_site_themes, check_themes
from apps.themes.tests.builders import write_theme


@pytest.fixture
def root(tmp_path: Path, settings: SettingsWrapper) -> Path:
    root = tmp_path / "themes"
    write_theme(root, "base")
    write_theme(root, "ride")
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    return root


def ids(messages: list[checks.CheckMessage]) -> list[str]:
    return [message.id or "" for message in messages]


def test_valid_themes_pass(root: Path) -> None:
    assert check_themes(None) == []


def test_the_shipped_themes_pass() -> None:
    assert check_themes(None) == []


def test_an_unknown_default_theme_fails(root: Path, settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "nope"
    errors = check_themes(None)
    assert ids(errors) == ["themes.E002"]
    assert "nope" in errors[0].msg


def test_a_registered_child_can_be_the_default(root: Path, settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    assert check_themes(None) == []


@pytest.mark.parametrize(
    "manifest",
    [
        'slug = "broken"\nname = "Broken"\nversion = "1"\nparent = "missing"\n',
        'slug = "broken"\nname = ""\nversion = "1"\nparent = "base"\n',
        "this is [not toml",
    ],
)
def test_an_invalid_manifest_fails(root: Path, manifest: str) -> None:
    write_theme(root, "broken", manifest=manifest)
    errors = check_themes(None)
    assert ids(errors) == ["themes.E001"]
    assert "themes/broken" in errors[0].msg


def test_a_directory_without_a_manifest_is_reported(root: Path) -> None:
    (root / "stray" / "templates").mkdir(parents=True)
    assert ids(check_themes(None)) == ["themes.E001"]


def test_a_missing_base_theme_fails(tmp_path: Path, settings: SettingsWrapper) -> None:
    root = tmp_path / "nobase"
    write_theme(root, "ride")
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "ride"
    assert "themes.E004" in ids(check_themes(None))


def test_the_checks_are_registered() -> None:
    registered = checks.registry.registry.get_checks()
    assert check_themes in registered
    # The stored site settings are read from the database: `migrate` and
    # `check --database default` run it.
    assert check_site_themes in registered
    assert checks.Tags.database in getattr(check_site_themes, "tags", ())
