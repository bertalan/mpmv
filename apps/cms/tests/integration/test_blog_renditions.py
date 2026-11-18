"""CAT.29: the blog article and author pages never build a rendition while rendering; they use the
ones the `core.render_image` job made (presets "article" and "avatar") or fall back to the
original image, and keep alt text, credit, width and height."""

import re

import pytest
from bs4 import BeautifulSoup
from django.test import Client

from apps.cms.models import BlogAuthor, BlogIndexPage
from apps.cms.services import SeedReport
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    BlogPostPageFactory,
)
from apps.core import images
from apps.core.models import ImageLicence, MediaImage, MediaImageRendition
from apps.core.tasks import render_image
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.fixture
def blog(seeded: SeedReport) -> BlogIndexPage:
    index: BlogIndexPage = BlogIndexPageFactory.create(live=False)
    index.save_revision().publish()
    index.refresh_from_db()
    return index


def _photo(name: str) -> MediaImage:
    image: MediaImage = MediaImageFactory.create(
        licence=ImageLicence.objects.get(code="CC-BY-4.0"),
        author="Ada Lovelace",
        source_url="https://example.com/source",
        file__filename=f"{name}.png",
        file__width=2000,
        file__height=1500,
    )
    return image


def _article(blog: BlogIndexPage, image: MediaImage) -> str:
    page = BlogPostPageFactory.create(
        parent=blog, title="Lake", image=image, image_alt_text="A lake", image_caption="Calm"
    )
    return str(page.url)


def _author(image: MediaImage) -> BlogAuthor:
    author = BlogAuthorFactory.create(name="Ada Example", photo=image)
    author.photo_alt_text = "Ada at the wheel"
    author.save()
    return author


def _soup(client: Client, path: str) -> BeautifulSoup:
    response = client.get(path)
    assert response.status_code == 200
    return BeautifulSoup(response.content.decode(), "html.parser")


def test_an_article_without_renditions_builds_none_and_shows_the_original(
    blog: BlogIndexPage, client: Client
) -> None:
    image = _photo("lake")
    url = _article(blog, image)
    before = MediaImageRendition.objects.count()

    figure = _soup(client, url).find("figure")

    assert MediaImageRendition.objects.count() == before
    assert figure is not None
    img = figure.find("img")
    assert img is not None
    assert (img["alt"], img["width"], img["height"]) == ("A lake", "2000", "1500")
    assert "lake" in str(img["src"])
    assert "Calm" in figure.get_text()
    assert "Ada Lovelace" in figure.get_text()


def test_an_article_with_renditions_uses_them_and_builds_none(
    blog: BlogIndexPage, client: Client
) -> None:
    image = _photo("lake")
    render_image(image_id=image.pk)
    url = _article(blog, image)
    before = MediaImageRendition.objects.count()

    figure = _soup(client, url).find("figure")

    assert MediaImageRendition.objects.count() == before
    assert figure is not None
    assert figure.find("picture") is not None
    img = figure.find("img")
    assert img is not None
    assert img["alt"] == "A lake"
    assert img["width"] == "1600"
    assert img["height"] == "1200"
    assert re.search(r"\.jpg \d+w", str(img["srcset"]))
    assert "Ada Lovelace" in figure.get_text()


def test_an_author_page_without_renditions_builds_none(blog: BlogIndexPage, client: Client) -> None:
    author = _author(_photo("ada-portrait"))
    BlogPostPageFactory.create(parent=blog, title="By Ada", author=author)
    before = MediaImageRendition.objects.count()

    figure = _soup(client, "/blog/author/ada-example/").find("figure")

    assert MediaImageRendition.objects.count() == before
    assert figure is not None
    img = figure.find("img")
    assert img is not None
    assert (img["alt"], img["width"], img["height"]) == ("Ada at the wheel", "2000", "1500")
    assert "Ada Lovelace" in figure.get_text()


def test_an_author_page_with_renditions_uses_the_square_crop(
    blog: BlogIndexPage, client: Client
) -> None:
    image = _photo("ada-portrait")
    render_image(image_id=image.pk)
    _author(image)
    before = MediaImageRendition.objects.count()

    figure = _soup(client, "/blog/author/ada-example/").find("figure")

    assert MediaImageRendition.objects.count() == before
    assert figure is not None
    img = figure.find("img")
    assert img is not None
    assert img["alt"] == "Ada at the wheel"
    assert img["width"] == "480"
    assert img["height"] == "480"
    assert "Ada Lovelace" in figure.get_text()


def test_the_render_job_makes_the_article_and_avatar_presets() -> None:
    assert {"article", "avatar"} <= set(images.PRESETS)
    image = _photo("any")
    render_image(image_id=image.pk)
    made = set(
        MediaImageRendition.objects.filter(image=image).values_list("filter_spec", flat=True)
    )
    formats = images.picture_formats()
    for name in ("article", "avatar"):
        assert set(images.picture_specs(name, formats=formats)) <= made
