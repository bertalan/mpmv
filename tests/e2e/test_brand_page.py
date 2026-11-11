"""E2E (CAT.20 3/3): a brand page in a real browser, with its content and structured data.

Done when: the brand page shows the logo, the description in the page language and the official
links, carries one JSON-LD graph with the brand `@id`, and triggers no CSP violation.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    ManufacturerFactory,
    brand_logo_image,
)
from apps.core.models import TranslationStatus
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def brand_page() -> None:
    brand = BrandFactory.create(
        name="Acme",
        wikidata_id="Q42",
        url="https://acme.example/",
        logo=brand_logo_image(),
        logo_alt="Acme anvil",
        manufacturer=ManufacturerFactory.create(legal_name="Acme Holdings Ltd", country="GB"),
    )
    for language, slug, text in (
        ("en", "acme", "Anvils since 1949."),
        ("it", "acme-it", "Incudini dal 1949."),
    ):
        BrandTranslationFactory.create(
            brand=brand,
            language=language,
            slug=slug,
            description=f"<p>{text}</p>",
            status=PUBLISHED,
        )


@pytest.mark.usefixtures("brand_page")
@pytest.mark.parametrize(
    ("path", "language", "text"),
    [
        ("/brand/acme/", "en", "Anvils since 1949."),
        ("/it/marchio/acme-it/", "it", "Incudini dal 1949."),
    ],
)
def test_a_brand_page_shows_its_content_and_structured_data(
    page: Page,
    live_server: LiveServer,
    shot: Callable[[str], Path],
    path: str,
    language: str,
    text: str,
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    response = page.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.status == 200
    expect(page.locator("html")).to_have_attribute("lang", language)
    expect(page.get_by_role("heading", level=1)).to_have_text("Acme")
    profile = page.locator(".brand-profile")
    expect(profile.locator(".brand-description")).to_have_text(text)
    expect(profile.get_by_role("img", name="Acme anvil")).to_be_visible()
    expect(profile.locator('a[href="https://www.wikidata.org/wiki/Q42"]')).to_be_visible()
    shot(language)
    graph = json.loads(page.locator('script[type="application/ld+json"]').inner_text())["@graph"]
    brand = next(node for node in graph if node["@type"] == "Brand")
    assert brand["@id"] == f"{live_server.url}/brand/acme/#brand"
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
