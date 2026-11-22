"""THM.36: `countdown.js` loads only on pages that have a launch countdown block (CMS.9)."""

import datetime as dt
from typing import Any

import pytest
from django.test import Client

from apps.cms.models import HomePage
from apps.cms.services import SeedReport, seed_site
from apps.core.i18n import primary_language
from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db

SCRIPT = "themes/base/js/lazy/countdown.js"
TARGET = dt.datetime(2030, 12, 1, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def seeded() -> SeedReport:
    return seed_site()


def _countdown() -> tuple[str, dict[str, Any]]:
    return (
        "countdown",
        {
            "target": TARGET,
            "heading": "The touring jacket",
            "body": "",
            "product": None,
            "category": None,
            "cta_label": "",
            "cta_link": "",
            "expired_text": "",
            "expired_link": "",
        },
    )


def _html(theme: str, *body: tuple[str, Any]) -> str:
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = list(body)
    home.save_revision().publish()
    with use_theme(theme):
        return Client().get(home.url).content.decode()


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_page_without_a_countdown_does_not_load_the_script(
    seeded: SeedReport, theme: str
) -> None:
    html = _html(theme)
    assert "themes/base/js/base.js" in html
    assert SCRIPT not in html


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_page_with_a_countdown_loads_the_script_once(seeded: SeedReport, theme: str) -> None:
    html = _html(theme, _countdown(), _countdown())
    assert html.count("data-countdown ") == 2
    assert html.count(SCRIPT) == 1
    assert 'type="module"' in html[html.index(SCRIPT) - 60 : html.index(SCRIPT)]
