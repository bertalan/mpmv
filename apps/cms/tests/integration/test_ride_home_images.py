"""THM.24 (part 2/2): pictures on the home tiles and the blog teaser cards.

A featured card has an optional picture; a card to a blog post falls back to the post's main
image. Tiles and teasers render it as a responsive `<picture>` (THM.12) and keep the placeholder
art when there is none.
"""

import re
from typing import Any

import pytest
from django.test import Client, override_settings

from apps.cms.blocks import PageCardBlock, ProductCardBlock, TermCardBlock, VendorCardBlock
from apps.cms.models import BlogPostPage, HomePage, StandardPage
from apps.cms.services import SeedReport
from apps.cms.tests.factories import BlogPostPageFactory
from apps.core.i18n import primary_language
from apps.core.models import MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def picture() -> MediaImage:
    image: MediaImage = MediaImageFactory.create(title="Tile", file__filename="tilepic.png")
    return image


@pytest.fixture
def post_image() -> MediaImage:
    image: MediaImage = MediaImageFactory.create(title="Post", file__filename="postpic.png")
    return image


def _home(body: list[Any]) -> str:
    home = HomePage.objects.get(locale__language_code=primary_language())
    home.body = body
    home.save_revision().publish()
    with override_settings(MPMV_THEME_DEFAULT="ride"):
        html = Client().get("/").content.decode()
    return html[html.index("<main") :]


def _tiles(html: str) -> list[str]:
    return [t[: t.index("</li>")] for t in re.split(r'(?=<li class="tile")', html)[1:]]


def _post(image: MediaImage | None = None, alt: str = "A bowl") -> BlogPostPage:
    post: BlogPostPage = BlogPostPageFactory.create(
        image=image, image_alt_text=alt if image else ""
    )
    post.save_revision().publish()
    return post


def test_the_card_blocks_have_an_optional_image() -> None:
    for block in (PageCardBlock(), TermCardBlock(), VendorCardBlock(), ProductCardBlock()):
        assert block.child_blocks["image"].required is False


def test_a_tile_with_a_picture_renders_it_responsive_and_decorative(
    seeded: SeedReport, picture: MediaImage
) -> None:
    about = StandardPage.objects.get(locale__language_code=primary_language(), depth=3)
    with_image = {"page": about, "label": "", "text": "", "image": picture}
    without = {"page": about, "label": "", "text": "", "image": None}
    html = _home(
        [("featured", {"heading": "Browse", "cards": [("page", with_image), ("page", without)]})]
    )
    first, second = _tiles(html)
    assert "<picture" in first
    assert "tilepic" in first
    assert re.search(r'<img[^>]* alt=""', first)  # the title is the link: the picture is decoration
    assert re.search(r'<img[^>]* width="\d+"', first)
    assert re.search(r'<img[^>]* height="\d+"', first)
    assert first.index("<picture") < first.index("tile__scrim")  # under the scrim
    assert "<picture" not in second  # the placeholder art stays


def test_a_tile_to_a_post_falls_back_to_the_post_image(
    seeded: SeedReport, post_image: MediaImage
) -> None:
    card = {"page": _post(post_image), "label": "", "text": "", "image": None}
    html = _home([("featured", {"heading": "Posts", "cards": [("page", card)]})])
    assert "postpic" in _tiles(html)[0]


def test_the_cards_own_picture_wins_over_the_posts(
    seeded: SeedReport, post_image: MediaImage, picture: MediaImage
) -> None:
    card = {"page": _post(post_image), "label": "", "text": "", "image": picture}
    html = _home([("featured", {"heading": "Posts", "cards": [("page", card)]})])
    tile = _tiles(html)[0]
    assert "tilepic" in tile
    assert "postpic" not in tile


def test_a_blog_teaser_shows_the_post_image(seeded: SeedReport, post_image: MediaImage) -> None:
    cards = [
        ("page", {"page": _post(post_image, "Bread"), "label": "", "text": "", "image": None}),
        ("page", {"page": _post(), "label": "", "text": "", "image": None}),
    ]
    html = _home([("strip", {"heading": "Blog", "cards": cards, "per_view": "2"})])
    items = re.split(r'(?=<li class="ride-strip__item")', html)[1:]
    assert len(items) == 2
    assert "<picture" in items[0]
    assert 'alt="Bread"' in items[0]
    assert "<picture" not in items[1]
