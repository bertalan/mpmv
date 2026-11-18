"""CMS.17: footer columns and social profiles from the per-site, per-language settings."""

import datetime as dt
import json
import re
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.admin.panels import get_edit_handler
from wagtail.models import Site

from apps.cms.models import FooterColumn, FooterSettings, SocialProfile
from apps.cms.tests.factories import (
    FooterColumnFactory,
    FooterSettingsFactory,
    SocialProfileFactory,
    StandardPageFactory,
)
from apps.core.i18n import primary_language
from apps.core.tests.factories import PlatformOperatorFactory
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db

LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _footer(html: str) -> str:
    return html.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]


def _settings() -> FooterSettings:
    return FooterSettingsFactory.create()


def _column(settings: FooterSettings, language: str, title: str, links: list[Any]) -> FooterColumn:
    return FooterColumnFactory.create(
        settings=settings, language=language, title=title, links=links
    )


def _profile(
    settings: FooterSettings, url: str, network: str = "mastodon", language: str = ""
) -> SocialProfile:
    return SocialProfileFactory.create(
        settings=settings, network=network, url=url, language=language
    )


@pytest.fixture(params=["base", "ride"])
def theme(request: pytest.FixtureRequest, settings: SettingsWrapper) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def test_columns_and_profiles_show_in_each_language_in_both_themes(
    client: Client, theme: str
) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    about = StandardPageFactory.create(title="About us", parent=home)
    s = _settings()
    primary, second = primary_language(), other_language()
    _column(
        s,
        primary,
        "Company",
        [
            ("page", {"page": about, "label": ""}),
            ("external", {"url": "https://example.com/press/", "label": "Press"}),
        ],
    )
    _column(
        s, second, "Azienda", [("external", {"url": "https://example.com/it/", "label": "Stampa"})]
    )
    _profile(s, "https://social.example/@shop", "mastodon")
    en = _footer(client.get(f"{language_prefix(primary)}/").content.decode())
    other = _footer(client.get(f"{language_prefix(second)}/").content.decode())
    assert "Company" in en
    assert "Press" in en
    assert "About us" in en
    assert "Azienda" not in en
    assert "Azienda" in other
    assert "Stampa" in other
    assert "Company" not in other
    for html in (en, other):
        assert 'href="https://social.example/@shop"' in html
        assert 'rel="me noopener' in html
        assert re.search(r">\s*Mastodon\s*</a>", html)  # a text label, not an icon alone


def test_nothing_set_renders_no_empty_column(client: Client, theme: str) -> None:
    html = _footer(client.get("/").content.decode())
    assert "site-footer__widget" not in html
    assert 'aria-label="Social"' not in html
    assert 'rel="me' not in html
    # an empty column (no title, no links) is not rendered either
    _column(_settings(), primary_language(), "", [])
    html = _footer(client.get("/").content.decode())
    assert "site-footer__widget" not in html


def test_a_profile_url_must_be_absolute_https() -> None:
    s = _settings()
    for bad in (
        "javascript:alert(1)",
        "http://social.example/@a",
        "/local",
        "https://u:p@x.example/",
    ):
        with pytest.raises(ValidationError) as caught:
            SocialProfile(settings=s, network="mastodon", url=bad).full_clean()
        assert "url" in caught.value.message_dict
    SocialProfile(settings=s, network="mastodon", url="https://social.example/@a").full_clean()


def test_the_network_comes_from_a_closed_list() -> None:
    with pytest.raises(ValidationError) as caught:
        SocialProfile(settings=_settings(), network="nope", url="https://x.example/").full_clean()
    assert "network" in caught.value.message_dict


def test_the_footer_form_refuses_a_fifth_column_in_a_language() -> None:
    form_class = get_edit_handler(FooterSettings).get_form_class()

    def data(count: int) -> dict[str, str]:
        out = {
            "columns-TOTAL_FORMS": str(count),
            "columns-INITIAL_FORMS": "0",
            "columns-MIN_NUM_FORMS": "0",
            "columns-MAX_NUM_FORMS": "1000",
            "profiles-TOTAL_FORMS": "0",
            "profiles-INITIAL_FORMS": "0",
            "profiles-MIN_NUM_FORMS": "0",
            "profiles-MAX_NUM_FORMS": "1000",
        }
        for n in range(count):
            out |= {
                f"columns-{n}-language": primary_language(),
                f"columns-{n}-title": f"C{n}",
                f"columns-{n}-links-count": "0",
                f"columns-{n}-ORDER": str(n + 1),
                f"columns-{n}-id": "",
            }
        return out

    assert form_class(data(4), instance=_settings()).is_valid()
    refused = form_class(data(5), instance=_settings())
    assert not refused.is_valid()
    assert "four" in str(refused.errors).lower()


def test_profiles_feed_the_organization_same_as(client: Client) -> None:
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        official_profiles=["https://www.wikidata.org/wiki/Q1"],
    )
    s = _settings()
    _profile(s, "https://social.example/@shop")
    _profile(s, "https://video.example/c/shop", "youtube", other_language())
    graph = []
    for match in LD.finditer(client.get("/").content.decode()):
        data = json.loads(match.group(1))
        graph += data.get("@graph", [data])
    org = next(n for n in graph if n.get("@type") == "Organization")
    assert "https://social.example/@shop" in org["sameAs"]
    assert "https://video.example/c/shop" in org["sameAs"]
    assert "https://www.wikidata.org/wiki/Q1" in org["sameAs"]
    assert org["@id"].endswith("/#organization")
