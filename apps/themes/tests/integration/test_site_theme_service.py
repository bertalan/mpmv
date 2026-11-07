"""THM.1: one theme per Wagtail Site, chosen through an audited service (spec 14 §14.3).

Selection order: the site's `ThemeSettings`, then `MPMV_THEME_DEFAULT`, then `base`.
"""

from pathlib import Path

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from pytest_django import DjangoAssertNumQueries
from pytest_django.fixtures import SettingsWrapper
from wagtail.models import Site

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.themes.engine import ThemeError
from apps.themes.models import ThemeSettings
from apps.themes.services import (
    THEME_CONFIGURED,
    THEME_SETTINGS_MODEL,
    active_theme,
    set_site_theme,
    site_theme_choice,
)
from apps.themes.tests.builders import write_theme
from apps.themes.tests.factories import SiteFactory, ThemeSettingsFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:theme-admin@example.com", source=AuditLog.Source.ADMIN)


@pytest.fixture(autouse=True)
def themes(tmp_path: Path, settings: SettingsWrapper) -> Path:
    root = tmp_path / "themes"
    write_theme(root, "base")
    write_theme(root, "ride")
    write_theme(root, "calm")
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    settings.ALLOWED_HOSTS = [".example.test"]
    return root


def request_for(site: Site) -> HttpRequest:
    return RequestFactory().get("/", HTTP_HOST=site.hostname)


def test_a_dry_run_reports_the_change_and_writes_nothing() -> None:
    site = SiteFactory.create()
    changes = set_site_theme(site, "ride", ctx=CTX, dry_run=True)
    assert [(c.op, c.model, c.key) for c in changes.changes] == [
        ("create", THEME_SETTINGS_MODEL, str(site.pk))
    ]
    assert changes.changes[0].after == {"site": site.hostname, "theme": "ride"}
    assert not ThemeSettings.objects.exists()
    assert not AuditLog.objects.exists()


def test_choosing_a_theme_saves_it_and_audits_before_and_after() -> None:
    site = SiteFactory.create()
    set_site_theme(site, "ride", ctx=CTX)
    set_site_theme(site, "calm", ctx=CTX)

    assert site_theme_choice(site) == "calm"
    entries = list(AuditLog.objects.filter(action=THEME_CONFIGURED).order_by("pk"))
    assert [(e.target_model, e.target_key) for e in entries] == [
        (THEME_SETTINGS_MODEL, str(site.pk)),
        (THEME_SETTINGS_MODEL, str(site.pk)),
    ]
    assert entries[0].before is None
    assert entries[1].before == {"site": site.hostname, "theme": "ride"}
    assert entries[1].after == {"site": site.hostname, "theme": "calm"}
    assert entries[1].actor_label == CTX.actor_label


def test_saving_the_same_theme_writes_no_audit_entry() -> None:
    site = SiteFactory.create()
    set_site_theme(site, "ride", ctx=CTX)
    assert set_site_theme(site, "ride", ctx=CTX).is_empty
    assert AuditLog.objects.filter(action=THEME_CONFIGURED).count() == 1


def test_an_empty_choice_goes_back_to_the_environment_default() -> None:
    site = SiteFactory.create()
    set_site_theme(site, "ride", ctx=CTX)
    set_site_theme(site, "", ctx=CTX)
    assert site_theme_choice(site) == ""
    assert active_theme(request_for(site)).slug == "base"


def test_an_unknown_theme_is_refused() -> None:
    site = SiteFactory.create()
    with pytest.raises(ThemeError, match="nope"):
        set_site_theme(site, "nope", ctx=CTX)
    assert not ThemeSettings.objects.exists()
    assert not AuditLog.objects.exists()


def test_each_site_gets_its_own_theme(settings: SettingsWrapper) -> None:
    ride = ThemeSettingsFactory.create(theme="ride").site
    calm = ThemeSettingsFactory.create(theme="calm").site
    plain = SiteFactory.create()

    assert active_theme(request_for(ride)).slug == "ride"
    assert active_theme(request_for(calm)).slug == "calm"
    assert active_theme(request_for(plain)).slug == "base"
    settings.MPMV_THEME_DEFAULT = "calm"
    assert active_theme(request_for(plain)).slug == "calm"


def test_a_stored_theme_that_was_removed_falls_back_to_the_default() -> None:
    site = ThemeSettingsFactory.create(theme="removed").site
    assert active_theme(request_for(site)).slug == "base"


def test_the_active_theme_is_resolved_once_per_request(
    django_assert_num_queries: DjangoAssertNumQueries,
) -> None:
    site = ThemeSettingsFactory.create(theme="ride").site
    request = request_for(site)
    first = active_theme(request)
    with django_assert_num_queries(0):
        assert active_theme(request) is first


def test_reading_the_theme_never_writes_a_settings_row() -> None:
    site = SiteFactory.create()
    active_theme(request_for(site))
    assert site_theme_choice(site) == ""
    assert not ThemeSettings.objects.exists()
