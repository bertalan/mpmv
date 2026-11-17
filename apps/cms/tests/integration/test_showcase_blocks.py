"""THM.14 (part 3/3, scope extension): gallery, counter, progress, section title and video
blocks that an editor places from the CMS. `ride` renders the motion components, `base` a plain,
non-animated fallback; the video accepts only YouTube and Vimeo addresses."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test.utils import override_settings
from wagtail import blocks

from apps.cms.blocks import (
    CounterBlock,
    GalleryBlock,
    ProgressBlock,
    SectionTitleBlock,
    VideoModalBlock,
)
from apps.cms.engine import parse_video_url
from apps.cms.models import HomePage
from apps.cms.services import seed_site
from apps.core.i18n import primary_language
from apps.core.tests.factories import MediaImageFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]

YOUTUBE = "https://www.youtube.com/watch?v=abcdefghijk"


def render(theme: str, block: blocks.Block, value: Any) -> str:
    with override_settings(MPMV_THEME_DEFAULT=theme):
        return str(block.render(value, context={})).strip()


def gallery_value(count: int = 3) -> Any:
    images = [
        {
            "image": MediaImageFactory.create(title=f"G{n}", file__filename=f"g{n}.png").pk,
            "alt_text": f"Picture {n}",
            "caption": f"Caption {n}",
            "language": "",
        }
        for n in range(1, count + 1)
    ]
    return GalleryBlock().to_python({"images": images})


@pytest.mark.parametrize("key", ["gallery", "counter", "progress", "section_title", "video_modal"])
def test_every_block_is_in_the_home_body(key: str) -> None:
    assert key in HomePage._meta.get_field("body").stream_block.child_blocks


@pytest.mark.parametrize(
    "url",
    [
        YOUTUBE,
        "https://youtu.be/abcdefghijk",
        "https://www.youtube-nocookie.com/embed/abcdefghijk",
        "https://vimeo.com/123456789",
        "https://player.vimeo.com/video/123456789",
    ],
)
def test_provider_urls_become_a_watch_link_and_a_no_tracking_embed(url: str) -> None:
    links = parse_video_url(url)
    assert links is not None
    assert links.embed_url.startswith(
        ("https://www.youtube-nocookie.com/embed/", "https://player.vimeo.com/video/")
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=abcdefghijk",
        "https://evil.example.com/watch?v=abcdefghijk",
        "https://www.youtube.com.evil.example/watch?v=abcdefghijk",
        "https://www.youtube.com/watch?v=",
        "https://vimeo.com/not-a-number",
        "javascript:alert(1)",
        "",
    ],
)
def test_other_urls_are_not_videos(url: str) -> None:
    assert parse_video_url(url) is None


def test_the_video_block_refuses_other_sites_and_needs_alt_text_with_a_cover() -> None:
    block = VideoModalBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python({"title": "Tour", "url": "https://example.com/v"}))
    cover = MediaImageFactory.create(title="Cover", file__filename="cover.png")
    with pytest.raises(ValidationError):
        block.clean(
            block.to_python({"title": "Tour", "url": YOUTUBE, "poster": cover.pk, "alt_text": ""})
        )
    block.clean(block.to_python({"title": "Tour", "url": YOUTUBE}))


def test_progress_value_cannot_exceed_the_maximum() -> None:
    block = ProgressBlock()
    with pytest.raises(ValidationError):
        block.clean(block.to_python({"label": "Packed", "value": 7, "max": 5}))
    block.clean(block.to_python({"label": "Packed", "value": 5, "max": 5}))


def test_ride_renders_the_gallery_as_a_carousel_with_thumbnails_and_base_as_a_list() -> None:
    value = gallery_value()
    ride = render("ride", GalleryBlock(), value)
    assert 'aria-roledescription="carousel"' in ride
    assert ride.count("data-bs-slide-to") == 3
    assert "Caption 2" in ride
    assert "data-autoplay" not in ride
    base = render("base", GalleryBlock(), value)
    assert 'class="gallery"' in base
    assert "carousel" not in base
    assert "Caption 2" in base


def test_ride_renders_counter_progress_and_section_title_from_the_components() -> None:
    counter = CounterBlock().to_python({"label": "Orders shipped", "value": 1250, "suffix": "+"})
    html = render("ride", CounterBlock(), counter)
    assert 'data-counter="1250"' in html
    assert "Orders shipped" in html
    progress = ProgressBlock().to_python({"label": "Packed", "value": 60, "max": 100})
    html = render("ride", ProgressBlock(), progress)
    assert "<progress" in html
    assert "60%" in html
    title = SectionTitleBlock().to_python({"eyebrow": "Our range", "title": "Gear", "lead": "Hi"})
    html = render("ride", SectionTitleBlock(), title)
    assert "ride-section-title__overline" in html
    assert "<h2" in html


def test_base_renders_the_fallbacks_without_animation() -> None:
    counter = CounterBlock().to_python({"label": "Orders shipped", "value": 1250, "suffix": "+"})
    html = render("base", CounterBlock(), counter)
    assert "1250+" in html
    assert "data-counter" not in html
    progress = ProgressBlock().to_python({"label": "Packed", "value": 3, "max": 4})
    html = render("base", ProgressBlock(), progress)
    assert "<progress" in html
    assert "3 / 4" in html
    title = SectionTitleBlock().to_python({"eyebrow": "Our range", "title": "Gear", "lead": ""})
    html = render("base", SectionTitleBlock(), title)
    assert "<h2>Gear</h2>" in html


def test_the_video_is_a_consent_gated_modal_in_ride_and_a_plain_link_in_base() -> None:
    value = VideoModalBlock().to_python({"title": "Workshop tour", "url": YOUTUBE})
    ride = render("ride", VideoModalBlock(), value)
    assert 'data-consent="marketing"' in ride
    assert 'data-embed="https://www.youtube-nocookie.com/embed/abcdefghijk"' in ride
    assert "<iframe" not in ride
    base = render("base", VideoModalBlock(), value)
    assert "<iframe" not in base
    assert 'href="https://www.youtube.com/watch?v=abcdefghijk"' in base
    assert "data-embed" not in base


def test_a_page_without_a_carousel_does_not_load_the_carousel_script(client: Any) -> None:
    seed_site()
    with override_settings(MPMV_THEME_DEFAULT="ride"):
        html = client.get("/").content.decode()
    assert "js/carousel.js" not in html


def test_a_page_with_a_gallery_loads_the_carousel_script(client: Any) -> None:
    seed_site()
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = [("gallery", gallery_value())]
    home.save_revision().publish()
    with override_settings(MPMV_THEME_DEFAULT="ride"):
        html = client.get("/").content.decode()
    assert "js/carousel.js" in html
    assert 'aria-roledescription="carousel"' in html


def test_vimeo_unlisted_addresses_keep_their_hash() -> None:
    links = parse_video_url("https://vimeo.com/123456789/abcdef0123")
    assert links is not None
    assert links.watch_url == "https://vimeo.com/123456789/abcdef0123"
    assert links.embed_url == "https://player.vimeo.com/video/123456789?h=abcdef0123&dnt=1"
    player = parse_video_url("https://player.vimeo.com/video/123456789?h=abcdef0123")
    assert player is not None
    assert player.embed_url == links.embed_url
    assert parse_video_url("https://vimeo.com/123456789/not-a-hash!") is None
    assert parse_video_url("https://vimeo.com/123456789/abcdef0123/extra") is None


VIDEO_ORIGINS = ("https://www.youtube-nocookie.com", "https://player.vimeo.com")


def _frame_src(response: Any) -> list[str]:
    for directive in response["Content-Security-Policy"].split(";"):
        name, _, sources = directive.strip().partition(" ")
        if name == "frame-src":
            return list(sources.split())
    return []


@pytest.mark.parametrize("theme", ["ride", "base"])
def test_a_page_with_a_video_allows_the_two_no_tracking_origins_in_frame_src(
    client: Any, theme: str
) -> None:
    seed_site()
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = [("video_modal", {"title": "Tour", "url": YOUTUBE})]
    home.save_revision().publish()
    with override_settings(MPMV_THEME_DEFAULT=theme):
        response = client.get("/")
    sources = _frame_src(response)
    assert set(VIDEO_ORIGINS) <= set(sources)
    assert "*" not in sources
    assert "<iframe" not in response.content.decode()  # nothing is framed before consent


def test_a_page_without_a_video_keeps_the_site_frame_src(client: Any) -> None:
    seed_site()
    response = client.get("/")
    assert not set(VIDEO_ORIGINS) & set(_frame_src(response))
