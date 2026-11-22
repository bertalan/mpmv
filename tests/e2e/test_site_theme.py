"""E2E (THM.1): staff choose the site's theme in the admin and the public pages switch to it.

The themes come from a temporary directory: `base` plus a test child theme `alt` that overrides
only the home page template, so the About page still comes from the app (through `base`).
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.cms.models import StandardPage
from apps.core.i18n import primary_language
from apps.themes.tests.builders import shipped_templates, write_theme
from tests.e2e.login import admin_login, verify_with_totp
from tests.e2e.test_audit_log import translated_pattern
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)

ALT_HOME = """{% extends "layouts/base.html" %}
{% block content %}<h1>Alt theme: {{ page.title }}</h1>{% endblock %}
"""


@pytest.fixture
def themes(tmp_path: Path, settings: SettingsWrapper) -> Path:
    root = tmp_path / "themes"
    write_theme(root, "base", templates=shipped_templates())
    write_theme(root, "alt", templates={"cms/home_page.html": ALT_HOME})
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    return root


def test_staff_switch_the_site_theme_and_visitors_see_it(
    page: Page, live_server: LiveServer, themes: Path, shot: Callable[[str], Path]
) -> None:
    page.goto(f"{live_server.url}/")
    expect(page.locator("html")).to_have_attribute("data-theme", "base")
    expect(page.get_by_role("heading", level=1)).to_have_text(translated("Home"))
    shot("before")

    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device), window=True)

    page.goto(f"{live_server.url}/django-admin/themes/themesettings/add/")
    page.get_by_label(translated("Site")).select_option(index=1)
    page.get_by_label(translated("Theme")).select_option("alt")
    shot("admin-form")
    page.get_by_role("button", name=translated("Save"), exact=True).click()
    expect(page.locator(".messagelist")).to_contain_text(
        translated_pattern("The {name} “{obj}” was added successfully.")
    )

    page.goto(f"{live_server.url}/")
    expect(page.locator("html")).to_have_attribute("data-theme", "alt")
    expect(page.get_by_role("heading", level=1)).to_have_text("Alt theme: Home")
    shot("alt-home")

    about = StandardPage.objects.get(locale__language_code=primary_language())  # seeded
    page.get_by_role("navigation", name=translated("Main")).get_by_role(
        "link", name=about.title
    ).click()
    expect(page).to_have_url(re.compile(rf"/{about.slug}/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text(about.title)
    expect(page.locator("html")).to_have_attribute("data-theme", "alt")

    page.goto(
        f"{live_server.url}/django-admin/core/auditlog/?action=themes.theme_settings.configured"
    )
    expect(page.locator("#result_list")).to_contain_text("themes.theme_settings.configured")
    shot("audit")
