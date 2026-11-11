"""THM.1: staff choose a site's theme in the admin (audited); a stored unknown theme fails the
database system check (themes.E003)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.core.models import AuditLog
from apps.themes.checks import check_site_themes
from apps.themes.models import ThemeSettings
from apps.themes.services import THEME_CONFIGURED
from apps.themes.tests.builders import write_theme
from apps.themes.tests.factories import SiteFactory, ThemeSettingsFactory

pytestmark = pytest.mark.django_db

ADD = "admin:themes_themesettings_add"


@pytest.fixture(autouse=True)
def themes(tmp_path: Path, settings: SettingsWrapper) -> Path:
    root = tmp_path / "themes"
    write_theme(root, "base")
    write_theme(root, "ride")
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    return root


@pytest.fixture
def staff_client(
    client: Client, superuser: Any, mfa_login: Callable[[Client, Any], Client]
) -> Client:
    return mfa_login(client, superuser)


def test_the_form_offers_the_registered_themes_and_the_default(staff_client: Client) -> None:
    response = staff_client.get(reverse(ADD))
    assert response.status_code == 200
    choices = list(response.context["adminform"].form.fields["theme"].choices)
    assert choices == [
        ("", "Environment default (MPMV_THEME_DEFAULT)"),
        ("base", "Base theme"),
        ("ride", "Ride theme"),
    ]


def test_staff_choose_a_theme_and_the_change_is_audited(staff_client: Client) -> None:
    site = SiteFactory.create()
    response = staff_client.post(reverse(ADD), {"site": site.pk, "theme": "ride"})
    assert response.status_code == 302, response.content
    setting = ThemeSettings.objects.get(site=site)
    assert setting.theme == "ride"

    change = reverse("admin:themes_themesettings_change", args=[setting.pk])
    response = staff_client.post(change, {"site": site.pk, "theme": "base"})
    assert response.status_code == 302, response.content

    entries = AuditLog.objects.filter(action=THEME_CONFIGURED).order_by("pk")
    assert [(e.before, e.after) for e in entries] == [
        (None, {"site": site.hostname, "theme": "ride"}),
        ({"site": site.hostname, "theme": "ride"}, {"site": site.hostname, "theme": "base"}),
    ]
    assert entries[0].actor_label.startswith("staff:")


def test_an_unregistered_theme_is_refused_by_the_form(staff_client: Client) -> None:
    site = SiteFactory.create()
    response = staff_client.post(reverse(ADD), {"site": site.pk, "theme": "nope"})
    assert response.status_code == 200
    assert response.context["adminform"].form.errors["theme"]
    assert not ThemeSettings.objects.exists()


def test_settings_are_never_deleted_from_the_admin(staff_client: Client) -> None:
    setting = ThemeSettingsFactory.create()
    delete = reverse("admin:themes_themesettings_delete", args=[setting.pk])
    assert staff_client.get(delete).status_code == 403


def test_a_stored_unknown_theme_fails_the_database_check() -> None:
    site = ThemeSettingsFactory.create(theme="removed").site
    errors = check_site_themes(None, databases=["default"])
    assert [e.id for e in errors] == ["themes.E003"]
    assert "removed" in errors[0].msg
    assert site.hostname in errors[0].msg
    with pytest.raises(SystemCheckError, match=r"themes\.E003"):
        call_command("check", "--database", "default")


def test_known_stored_themes_pass_the_database_check() -> None:
    ThemeSettingsFactory.create(theme="ride")
    ThemeSettingsFactory.create(theme="")
    assert check_site_themes(None, databases=["default"]) == []


def _themes_migration_unapplied() -> None:
    from django.db.migrations.recorder import MigrationRecorder

    MigrationRecorder.Migration.objects.filter(app="themes", name="0001_initial").delete()


def _drop_theme_column() -> None:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # no pending trigger events
        cursor.execute(f'ALTER TABLE "{ThemeSettings._meta.db_table}" DROP COLUMN "theme"')


def test_a_pending_themes_migration_skips_the_site_check() -> None:
    """`migrate` runs the database checks before it applies anything (CORE.18 follow-up)."""
    ThemeSettingsFactory.create(theme="removed")
    _themes_migration_unapplied()
    assert check_site_themes(None, databases=["default"]) == []
    call_command("migrate", "--plan", skip_checks=False)


def test_a_missing_column_does_not_block_migrate() -> None:
    """A later migration may add a column: the table exists, the column does not yet."""
    ThemeSettingsFactory.create(theme="removed")
    _drop_theme_column()
    assert check_site_themes(None, databases=["default"]) == []
    _themes_migration_unapplied()
    call_command("migrate", "--plan", skip_checks=False)


def test_the_site_check_still_reports_on_a_fully_migrated_database() -> None:
    ThemeSettingsFactory.create(theme="removed")
    with pytest.raises(SystemCheckError, match=r"themes\.E003"):
        call_command("migrate", "--plan", skip_checks=False)


def test_without_a_database_the_site_check_does_nothing() -> None:
    ThemeSettingsFactory.create(theme="removed")
    assert check_site_themes(None) == []
