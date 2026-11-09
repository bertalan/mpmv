"""E2E (THM.12): a page's images come as `<picture>` with AVIF and WebP sources.

Done when: "an e2e test checks that a page's <picture> has avif/webp sources and no CSP
violation". The images are served same-origin by the media view; Chromium decodes AVIF, so it
picks the AVIF source.
"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import StandardPage
from apps.cms.tests.factories import StandardPageFactory
from apps.core.models import MediaImage
from apps.core.tests.factories import MediaImageFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _photo(name: str) -> MediaImage:
    image: MediaImage = MediaImageFactory.create(
        file__filename=f"{name}.png", file__width=1600, file__height=1000, file__color="teal"
    )
    return image


def test_a_page_serves_avif_and_webp_pictures_with_no_csp_violation(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    cms_page: StandardPage = StandardPageFactory.create(
        live=False,
        slug="formats",
        image=_photo("lead"),
        image_alt_text="A teal field",
        body=[("figure", {"image": _photo("figure"), "alt_text": "Hills", "caption": ""})],
    )
    cms_page.save_revision().publish()
    cms_page.refresh_from_db()
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}{cms_page.url}")

    pictures = page.locator("picture")
    expect(pictures).to_have_count(2)
    for index in range(2):
        picture = pictures.nth(index)
        expect(picture.locator('source[type="image/avif"]')).to_have_count(1)
        expect(picture.locator('source[type="image/webp"]')).to_have_count(1)
        img = picture.locator("img")
        expect(img).to_have_attribute("sizes", re.compile(r".+"))
        expect(img).to_have_attribute("width", re.compile(r"\d+"))
        expect(img).to_have_attribute("height", re.compile(r"\d+"))
        img.scroll_into_view_if_needed()
        page.wait_for_function(
            "img => img.complete && img.naturalWidth > 0", arg=img.element_handle()
        )
        current = img.evaluate("img => img.currentSrc")
        assert current.endswith(".avif"), current
    expect(pictures.first.locator("img")).to_have_attribute("fetchpriority", "high")
    shot("pictures")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))
