"""SEO.8 2/2: the platform-opening checklist reminder, and the audit entry of the toggle."""

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from wagtail.models import Site

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms import services as cms_services
from apps.core.models import AuditLog
from apps.seo import services
from apps.seo.models import SeoSettings
from tests.i18n import translated

pytestmark = pytest.mark.django_db

HEADING = translated("Before opening the platform")
STEP = "Publish the terms of sale in every language."


def open_steps(monkeypatch: pytest.MonkeyPatch, steps: list[str]) -> None:
    """The go-live gate reports `steps` (the real legal and DSA checks depend on seeded pages)."""
    monkeypatch.setattr(cms_services, "golive_problems", lambda languages=None: list(steps))


def panel(client: Client) -> str:
    return client.get(reverse("seo_admin:settings")).content.decode()


def home(client: Client) -> str:
    return client.get(reverse("wagtailadmin_home")).content.decode()


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_the_reminder_shows_open_steps_in_the_panel_and_on_the_home_page(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_steps(monkeypatch, [STEP])
    verified_login(client, UserFactory.create(superuser=True))
    for html in (panel(client), home(client)):
        assert HEADING in html
        assert STEP in html


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_the_reminder_is_hidden_when_every_step_is_done(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_steps(monkeypatch, [])
    verified_login(client, UserFactory.create(superuser=True))
    assert HEADING not in panel(client)
    assert HEADING not in home(client)


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_the_real_indexing_step_goes_away_when_indexing_is_turned_on() -> None:
    def indexing_open() -> bool:
        return any("indexing is still off" in step for step in services.opening_checklist())

    assert indexing_open()
    user = UserFactory.create(superuser=True)
    site = Site.objects.get(is_default_site=True)
    services.set_site_indexing(user, site, True, ctx=None)
    assert not indexing_open()


@override_settings(FORCE_NOINDEX=True)
def test_the_reminder_is_silent_under_the_floor(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_steps(monkeypatch, [STEP])
    assert services.opening_checklist() == []
    verified_login(client, UserFactory.create(superuser=True))
    assert HEADING not in panel(client)
    assert HEADING not in home(client)


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_the_home_page_reminder_is_for_platform_staff_only(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_steps(monkeypatch, [STEP])
    verified_login(client, UserFactory.create(is_staff=True))
    assert HEADING not in home(client)


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_turning_the_toggle_on_and_off_writes_one_audit_entry_each() -> None:
    user = UserFactory.create(superuser=True)
    site = Site.objects.get(is_default_site=True)
    services.set_site_indexing(user, site, True, ctx=None)
    services.set_site_indexing(user, site, False, ctx=None)
    entries = AuditLog.objects.filter(action=services.SEO_SETTINGS_CONFIGURED).order_by("pk")
    assert [(e.after or {}).get("allow_indexing") for e in entries] == [True, False]
    assert SeoSettings.objects.get(site=site).allow_indexing is False
