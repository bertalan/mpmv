"""THM.28: the `ride` blog index, post and author page (ride fidelity 10/12).

Image-left cards with date and author, a featured first post on the first page, the post meta
line, the reading column and the figure; no comment or share UI; the author page uses the card.
"""

from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.cms.models import BlogPostPage
from apps.core.tests.factories import MediaImageFactory
from tests.ride_content_site import build_content_site

pytestmark = pytest.mark.django_db


def soup(client: Client, path: str, settings: SettingsWrapper) -> Any:
    settings.MPMV_THEME_DEFAULT = "ride"
    response = client.get(path)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


@pytest.fixture
def paths(settings: SettingsWrapper) -> dict[str, str]:
    settings.MPMV_THEME_DEFAULT = "ride"
    return build_content_site(posts=3, images=True)


def test_the_first_post_of_the_first_page_is_featured_the_rest_are_cards(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    cards = soup(client, paths["blog"], settings).select("article.post-summary")
    assert len(cards) == 3
    assert "post-summary--featured" in cards[0]["class"]
    assert all("post-summary--featured" not in c["class"] for c in cards[1:])
    # The surface paints its own background, so it names its own text and link colours.
    assert "surface-band" in cards[0]["class"]
    assert all("surface-light" in c["class"] for c in cards[1:])


def test_a_card_has_the_image_left_the_date_the_author_and_one_link(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    # Newest first: "Workshop visit 2" is the featured card, "visit 0" (with the image) the last.
    cards = soup(client, paths["blog"], settings).select("article.post-summary")
    card = cards[-1]
    assert card.select_one(".post-summary__media picture img[alt='A workshop bench']")
    assert card.select_one("time[datetime]") is not None
    assert card.select_one(".article-author a") is not None
    assert [a["href"] for a in card.select("h2 a")] == [paths["post"]]
    assert card.select_one(".post-summary__media a, .post-summary__media [tabindex]") is None


def test_the_second_page_has_no_featured_post(client: Client, settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    paths = build_content_site(posts=13)
    second = soup(client, f"{paths['blog']}?page=2", settings)
    assert not second.select(".post-summary--featured")


def test_the_post_has_a_meta_line_a_reading_column_and_a_figure(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    page = soup(client, paths["post"], settings)
    article = page.select_one("main article.blog-post")
    assert article is not None
    meta = article.select_one(".article-meta")
    assert meta is not None
    assert meta.select_one("time[datetime]") is not None
    assert article.select_one(".reading-column") is not None
    assert article.select_one(".reading-column figure.post-figure picture") is not None


@pytest.mark.parametrize("kind", ["blog", "post", "author"])
def test_there_is_no_comment_or_share_ui(
    kind: str, client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    page = soup(client, paths[kind], settings)
    main = page.find("main")
    assert main is not None
    assert not main.select("[class*=comment], [class*=share], #comments, textarea")
    assert not main.select(
        "form[action*=comment], form[action*=share], a[href*=share], a[href*=comment]"
    )


def test_the_author_page_uses_the_same_card(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    blog = soup(client, paths["blog"], settings).select("article.post-summary")
    author = soup(client, paths["author"], settings).select("article.post-summary")
    assert len(author) == len(blog) == 3
    assert [c["class"] for c in author] == [c["class"] for c in blog]


def test_the_featured_card_shows_the_newest_post_image_on_the_band(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    featured = soup(client, paths["blog"], settings).select_one("article.post-summary--featured")
    assert featured is not None
    assert "surface-band" in featured["class"]
    img = featured.select_one(".post-summary__media picture img")
    assert img is not None
    assert img["alt"] == "Bench 2"


def test_the_index_page_does_not_query_per_post_image(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    few = build_content_site(posts=3, images=True)
    client.get(few["blog"])  # warm caches (site, settings, translations)

    def queries(path: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(path).status_code == 200
        return len(captured)

    base = queries(few["blog"])
    # Every post of a page has an image: the count must not grow with the posts.
    for post in BlogPostPage.objects.all():
        if post.image_id is None:
            post.image = MediaImageFactory.create(file__width=1600, file__height=900)
            post.image_alt_text = "A bench"
            post.save()
    with_images = queries(few["blog"])
    assert with_images <= base + 1
