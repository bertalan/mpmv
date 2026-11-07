"""THM.1 done-when: two Wagtail sites render different themes; a template missing in the child
theme comes from `base` (spec 14 §14.3).

Both sites serve the same CMS page tree, so any difference in the HTML comes from the theme.
"""

from pathlib import Path

import pytest
from django.test import Client
from pytest_django.fixtures import SettingsWrapper
from wagtail.models import Site

from apps.cms.services import seed_site
from apps.cms.tests.factories import StandardPageFactory
from apps.themes.tests.builders import write_theme
from apps.themes.tests.factories import SiteFactory, ThemeSettingsFactory

pytestmark = pytest.mark.django_db

ALT_HOME = """{% extends "cms/base.html" %}
{% block content %}<h1 class="alt-home">Alt theme: {{ page.title }}</h1>{% endblock %}
"""
BASE_STANDARD = """{% extends "cms/base.html" %}
{% block content %}<h1 class="base-standard">Base theme: {{ page.title }}</h1>{% endblock %}
"""


@pytest.fixture
def sites(tmp_path: Path, settings: SettingsWrapper) -> tuple[Site, Site]:
    root = tmp_path / "themes"
    write_theme(root, "base", templates={"cms/standard_page.html": BASE_STANDARD})
    write_theme(root, "alt", templates={"cms/home_page.html": ALT_HOME})
    settings.MPMV_THEMES_DIR = root
    settings.MPMV_THEME_DEFAULT = "base"
    settings.ALLOWED_HOSTS = [".example.test"]
    seed_site()
    plain = SiteFactory.create(hostname="plain.example.test")
    themed = ThemeSettingsFactory.create(
        site=SiteFactory.create(hostname="alt.example.test"), theme="alt"
    ).site
    return plain, themed


def get(client: Client, site: Site, path: str) -> str:
    response = client.get(path, HTTP_HOST=site.hostname)
    assert response.status_code == 200
    return response.content.decode()


def test_two_sites_render_different_themes(client: Client, sites: tuple[Site, Site]) -> None:
    plain, themed = sites
    plain_html = get(client, plain, "/")
    themed_html = get(client, themed, "/")

    assert 'data-theme="base"' in plain_html
    assert "Alt theme: Home" not in plain_html
    assert 'data-theme="alt"' in themed_html
    assert '<h1 class="alt-home">Alt theme: Home</h1>' in themed_html


def test_a_template_missing_in_the_child_comes_from_base(
    client: Client, sites: tuple[Site, Site]
) -> None:
    _, themed = sites
    StandardPageFactory.create(title="Shipping", slug="shipping")
    html = get(client, themed, "/shipping/")
    assert 'data-theme="alt"' in html
    assert '<h1 class="base-standard">Base theme: Shipping</h1>' in html


def test_a_template_in_no_theme_comes_from_the_app(
    client: Client, sites: tuple[Site, Site], tmp_path: Path
) -> None:
    plain, _ = sites
    # `base` overrides no home template here: the CMS app's own template renders it.
    html = get(client, plain, "/")
    assert "<h1>Home</h1>" in html


def test_the_admin_is_never_themed(
    client: Client, sites: tuple[Site, Site], tmp_path: Path
) -> None:
    write_theme(
        tmp_path / "themes",
        "alt",
        templates={"admin/login.html": "themed admin", "wagtailadmin/login.html": "themed"},
    )
    _, themed = sites
    for path in ("/django-admin/login/", "/admin/login/"):
        html = get(client, themed, path)
        assert "themed" not in html
