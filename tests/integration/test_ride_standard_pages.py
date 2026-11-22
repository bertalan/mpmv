"""THM.29: the `ride` standard, legal, feature-row and contact pages (ride fidelity 11/12).

A standard page with an image is a split block (image and text); the legal page keeps a stable
anchor on each required section and its print rules; the featured links are an icon feature row;
the contact points and the editor-built form page are an address column and a form card.
"""

import re
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.cms.tests.factories import FormPageFactory, LegalPageFactory, StandardPageFactory
from apps.core.tests.factories import MediaImageFactory
from apps.themes.services import use_theme
from tests.ride_content_site import build_content_site

pytestmark = pytest.mark.django_db

CSS = Path("themes/ride/static/themes/ride/css/48-content.css")


def soup(client: Client, path: str, settings: SettingsWrapper) -> Any:
    settings.MPMV_THEME_DEFAULT = "ride"
    response = client.get(path)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def test_a_standard_page_with_an_image_is_a_split_block(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    build_content_site()
    page = StandardPageFactory.create(
        title="About us",
        image=MediaImageFactory.create(file__width=1600, file__height=1200),
        image_alt_text="The workshop",
        image_caption="Our bench",
    )
    page.save_revision().publish()
    doc = soup(client, page.url, settings)
    split = doc.select_one(".split")
    assert split is not None
    assert split.select_one(".split__media figure picture img[alt='The workshop']")
    assert split.select_one(".split__media figcaption").get_text(strip=True).startswith("Our bench")
    assert "Sample text." in split.select_one(".split__text").get_text()
    assert len(doc.select("h1")) == 1


def test_a_standard_page_without_an_image_is_not_split(
    client: Client, settings: SettingsWrapper
) -> None:
    paths = build_content_site()
    doc = soup(client, paths["standard"], settings)
    assert doc.select_one(".split") is None
    assert doc.select_one(".page-content") is not None


def test_each_legal_section_keeps_an_anchor_and_its_data_hook(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    build_content_site()
    page = LegalPageFactory.create(
        title="Terms",
        body=[
            ("legal_section", {"section": "restrictions", "heading": "Limits", "text": "<p>x</p>"}),
            ("legal_section", {"section": "termination", "heading": "Ending", "text": "<p>y</p>"}),
        ],
    )
    page.save_revision().publish()
    doc = soup(client, page.url, settings)
    sections = doc.select("section.block-legal-section[data-section]")
    assert [s["id"] for s in sections] == ["section-restrictions", "section-termination"]
    for section in sections:
        assert section["id"] == f"section-{section['data-section']}"
        assert section.select_one("h2") is not None


def test_the_ride_bundle_prints_a_legal_page_without_the_chrome() -> None:
    css = CSS.read_text()
    block = re.search(r"@media print \{(.*?)\n  \}\n", css, re.S)
    assert block is not None
    for selector in (".page-banner", ".legal-page"):
        assert selector in block.group(1)


def test_the_featured_links_are_an_icon_feature_row() -> None:
    cards = [
        {"href": "/a/", "label": "Shipping", "text": "How we pack"},
        {"href": "/b/", "label": "Returns", "text": ""},
    ]
    with use_theme("ride"):
        html = render_to_string(
            "cms/blocks/featured.html", {"cards": cards, "value": {"heading": "Good to know"}}
        )
    doc = BeautifulSoup(html, "html.parser")
    items = doc.select("ul.feature-row > li")
    assert len(items) == 2
    for item in items:
        assert item.select_one("svg.icon[aria-hidden='true']") is not None
        assert len(item.select("a")) == 1
    assert doc.select_one("h2").get_text() == "Good to know"


def test_the_contact_points_are_an_address_column_and_a_form_card(
    client: Client, settings: SettingsWrapper
) -> None:
    paths = build_content_site()
    doc = soup(client, paths["contact"], settings)
    assert doc.select_one(".contact-layout .contact-address address") is not None
    card = doc.select_one(".contact-layout .contact-form-card.card")
    assert card is not None
    assert card.select_one("a[href='https://example.com/contact']") is not None
    assert len(doc.select("h1")) == 1


def test_the_form_page_is_a_banner_and_a_form_card(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    build_content_site()
    page = FormPageFactory.create(title="Write to us", intro="<p>We reply in two days.</p>")
    page.save_revision().publish()
    doc = soup(client, page.url, settings)
    assert [h.get_text(strip=True) for h in doc.select("h1")] == ["Write to us"]
    card = doc.select_one(".contact-layout .contact-form-card.card form[method='post']")
    assert card is not None
    assert card.select_one("input[name='csrfmiddlewaretoken']") is not None
    assert "We reply in two days." in doc.select_one(".contact-layout").get_text()
