"""E2E (THM.14 part 3/3): a page with every motion block placed from the CMS.

Under `ride` the page is axe `wcag22aa` and CSP clean at 360, 768 and 1280 px, and the carousels
work with the keyboard. Under `base` the same body renders its plain fallbacks without errors.
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage
from apps.core.i18n import primary_language
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_ride_carousels import slide

pytestmark = pytest.mark.django_db(transaction=True)

QUOTES = [
    {"type": "testimonial", "value": {"quote": f"Quote {n}", "author": f"Rider {n}", "role": ""}}
    for n in range(1, 4)
]


@pytest.fixture
def _showcase_home(live_server: LiveServer, _cms_site: None) -> None:
    from apps.core.tests.factories import MediaImageFactory

    images = [
        MediaImageFactory.create(title=f"G{n}", file__filename=f"g{n}.png") for n in (1, 2, 3)
    ]
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = [
        ("section_title", {"eyebrow": "Our range", "title": "Gear for every ride", "lead": "Hi"}),
        (
            "gallery",
            {
                "images": [
                    {
                        "image": image,
                        "alt_text": f"Picture {n}",
                        "caption": f"Caption {n}",
                        "language": "",
                    }
                    for n, image in enumerate(images, 1)
                ]
            },
        ),
        ("strip", {"heading": "Voices", "cards": QUOTES, "per_view": "2"}),
        ("counter", {"label": "Orders shipped", "value": 1250, "suffix": "+"}),
        ("progress", {"label": "Order packed", "value": 60, "max": 100}),
        (
            "video_modal",
            {"title": "Workshop tour", "url": "https://www.youtube.com/watch?v=abcdefghijk"},
        ),
    ]
    home.save_revision().publish()


def open_home(page: Page, live_server: LiveServer, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/")
    page.wait_for_load_state("load")


@pytest.mark.usefixtures("_showcase_home")
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_every_block_renders_under_ride_axe_and_csp_clean(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    open_home(page, live_server, width)
    expect(page.locator(".ride-section-title")).to_have_count(1)
    expect(page.locator(".ride-gallery")).to_have_count(1)
    expect(page.locator(".ride-strip")).to_have_count(1)
    expect(page.locator("[data-counter]")).to_have_count(1)
    expect(page.locator("progress")).to_have_count(1)
    expect(page.locator("[data-video]")).to_have_count(1)
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("_showcase_home")
def test_the_gallery_and_the_strip_work_with_the_keyboard(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    open_home(page, live_server, 1280)
    gallery = page.locator("#gallery-1")
    expect(gallery.locator(".carousel-item.active")).to_have_attribute("aria-label", slide(1))
    gallery.locator("[data-bs-slide=next]").focus()
    page.keyboard.press("Enter")
    expect(gallery.locator(".carousel-item.active")).to_have_attribute("aria-label", slide(2))
    page.wait_for_timeout(1200)
    expect(gallery.locator(".carousel-item.active")).to_have_attribute("aria-label", slide(2))
    strip = page.locator("#strip-1")
    strip.locator("[data-bs-slide=next]").focus()
    page.keyboard.press("Enter")
    expect(strip.locator(".carousel-item.active")).to_have_count(1)


@pytest.mark.usefixtures("_showcase_home")
@pytest.mark.parametrize("width", [360, 1280])
def test_base_renders_the_fallbacks_without_errors(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "base"
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_home(page, live_server, width)
    expect(page.locator(".block-counter")).to_contain_text("1250+")
    expect(page.locator("progress")).to_have_count(1)
    expect(page.locator("a[href='https://www.youtube.com/watch?v=abcdefghijk']")).to_have_count(1)
    expect(page.locator("[data-bs-slide]")).to_have_count(0)
    assert errors == []
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("_showcase_home")
def test_the_video_frames_the_no_tracking_embed_only_after_marketing_consent(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    """The frame is created only with consent to `marketing`; its address is the nocookie one.
    The network is not needed: the test reads the frame's `src`, not its content."""
    from apps.seo import engine

    settings.MPMV_THEME_DEFAULT = "ride"
    open_home(page, live_server, 1280)
    page.locator(".ride-video__open").click()
    expect(page.locator("[data-video-gate]")).to_be_visible()
    expect(page.locator("[data-video] iframe")).to_have_count(0)  # no consent: no frame

    cookie = engine.encode_cookie(
        engine.ConsentState(True, frozenset({"marketing"}), engine.BANNER_VERSION, "0" * 32)
    )
    page.context.add_cookies([{"name": "mpmv_consent", "value": cookie, "url": live_server.url}])
    open_home(page, live_server, 1280)
    page.locator(".ride-video__open").click()
    expect(page.locator("[data-video] iframe")).to_have_attribute(
        "src", "https://www.youtube-nocookie.com/embed/abcdefghijk"
    )
