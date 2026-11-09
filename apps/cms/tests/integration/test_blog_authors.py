"""CMS.3: blog authors and their pages (owner answer on PR #156).

An author has a name, a bio, a photo and an author page; the bio is translated, the name is
not. The author page of a language, `/blog/author/<slug>/` under that language's blog, shows the
bio and photo and lists the author's live articles of that language, newest first, a page of
them at a time; an author without articles gets the bio and an empty-state message. The byline
on articles and in the lists links to it.
"""

import datetime as dt
import json
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from wagtail.models import Locale

from apps.cms.models import BlogAuthor, BlogIndexPage, BlogPostPage, HomePage
from apps.cms.services import ArticleData, SeedReport, import_article, translate_page
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    BlogPostPageFactory,
)
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation when the transaction commits; a test runs in
    one transaction that never commits, so the hooks run at once here."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.fixture
def blog(seeded: SeedReport) -> BlogIndexPage:
    index: BlogIndexPage = BlogIndexPageFactory.create(live=False)
    index.save_revision().publish()
    index.refresh_from_db()
    return index


@pytest.fixture
def ada() -> BlogAuthor:
    author: BlogAuthor = BlogAuthorFactory.create(
        name="Ada Example", bio="<p>Ada throws <b>bowls</b> on the wheel.</p>"
    )
    return author


def _locale(code: str) -> Locale:
    locale: Locale = Locale.objects.get(language_code=code)
    return locale


def _html(path: str) -> BeautifulSoup:
    response = Client().get(path)
    assert response.status_code == 200, path
    return BeautifulSoup(response.content, "html.parser")


def _titles(page: BeautifulSoup) -> list[str]:
    return [h.get_text(strip=True) for h in page.select("article.post-summary h2")]


# --- The author snippet


def test_the_slug_follows_the_name(ada: BlogAuthor) -> None:
    assert ada.slug == "ada-example"


def test_a_name_is_unique_within_a_language(ada: BlogAuthor) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        BlogAuthor.objects.create(name="Ada Example", locale=ada.locale)


def test_a_photo_needs_alt_text(ada: BlogAuthor) -> None:
    ada.photo = MediaImageFactory.create()
    with pytest.raises(ValidationError):
        ada.full_clean()
    ada.photo_alt_text = "Ada at the wheel"
    ada.full_clean()


# --- The author page


def test_the_author_page_shows_the_bio_and_lists_the_live_articles_newest_first(
    blog: BlogIndexPage, ada: BlogAuthor
) -> None:
    other = BlogAuthorFactory.create(name="Bea Other")
    BlogPostPageFactory.create(parent=blog, title="Older", author=ada, date=dt.date(2024, 1, 5))
    BlogPostPageFactory.create(parent=blog, title="Newer", author=ada, date=dt.date(2025, 6, 1))
    BlogPostPageFactory.create(parent=blog, title="Draft", author=ada, live=False)
    BlogPostPageFactory.create(parent=blog, title="By Bea", author=other)

    page = _html("/blog/author/ada-example/")
    assert page.h1 is not None
    assert page.h1.get_text(strip=True) == "Ada Example"
    assert page.find("b", string="bowls") is not None
    assert _titles(page) == ["Newer", "Older"]
    assert page.title is not None
    assert "Ada Example" in page.title.get_text()


def test_the_author_page_shows_the_photo_with_its_alt_text(
    blog: BlogIndexPage, ada: BlogAuthor
) -> None:
    ada.photo = MediaImageFactory.create(file__filename="ada-portrait.png")
    ada.photo_alt_text = "Ada at the wheel"
    ada.save()

    img = _html("/blog/author/ada-example/").find("img", attrs={"alt": "Ada at the wheel"})
    assert img is not None
    assert "ada-portrait" in str(img["src"])


def test_the_author_page_is_paginated(blog: BlogIndexPage, ada: BlogAuthor) -> None:
    for day in range(1, 15):
        BlogPostPageFactory.create(
            parent=blog, title=f"Day {day}", author=ada, date=dt.date(2025, 1, day)
        )

    first = _html("/blog/author/ada-example/")
    second = _html("/blog/author/ada-example/?page=2")
    assert len(first.select("article.post-summary")) == 12
    assert _titles(second) == ["Day 2", "Day 1"]
    assert first.find("a", attrs={"rel": "next"}) is not None
    assert Client().get("/blog/author/ada-example/?page=9").status_code == 404


def test_an_author_without_articles_gets_the_bio_and_an_empty_state(
    blog: BlogIndexPage, ada: BlogAuthor
) -> None:
    page = _html("/blog/author/ada-example/")
    assert page.find("b", string="bowls") is not None
    assert not page.select("article.post-summary")
    assert "No articles by this author yet." in page.get_text()


def test_an_unknown_author_is_not_found(blog: BlogIndexPage) -> None:
    assert Client().get("/blog/author/nobody/").status_code == 404


def test_the_byline_links_to_the_author_page(blog: BlogIndexPage, ada: BlogAuthor) -> None:
    post = BlogPostPageFactory.create(parent=blog, title="Spring news", author=ada)

    for path in (post.url, blog.url, "/blog/author/ada-example/"):
        byline = _html(path).select_one(".article-author a")
        assert byline is not None, path
        assert byline["href"] == "/blog/author/ada-example/", path
        assert byline.get_text(strip=True) == "Ada Example", path


# --- Languages


def test_a_translated_article_brings_its_author_and_the_bio_is_translated(
    blog: BlogIndexPage, ada: BlogAuthor
) -> None:
    post = BlogPostPageFactory.create(parent=blog, title="Spring news", author=ada)
    translate_page(blog, ["it"])
    translate_page(post, ["it"])

    it_ada = BlogAuthor.objects.get(translation_key=ada.translation_key, locale=_locale("it"))
    assert it_ada.name == "Ada Example"
    assert it_ada.slug == "ada-example"
    it_post = BlogPostPage.objects.get(translation_key=post.translation_key, locale=_locale("it"))
    assert it_post.author == it_ada
    it_ada.bio = "<p>Ada modella <i>ciotole</i> al tornio.</p>"
    it_ada.save()

    it_page = _html("/it/blog/author/ada-example/")
    assert it_page.find("i", string="ciotole") is not None
    assert _titles(it_page) == ["Spring news"]
    en_page = _html("/blog/author/ada-example/")
    assert en_page.find("b", string="bowls") is not None
    assert _titles(en_page) == ["Spring news"]


def test_the_import_reuses_an_author_of_another_language_as_its_translation(
    seeded: SeedReport, ada: BlogAuthor
) -> None:
    it_blog: BlogIndexPage = BlogIndexPageFactory.create(
        parent=HomePage.objects.get(locale__language_code="it")
    )
    changes = import_article(
        it_blog,
        ArticleData(
            title="Una visita", slug="una-visita", date=dt.date(2023, 4, 12), author="Ada Example"
        ),
    )

    it_ada = BlogPostPage.objects.get().author
    assert it_ada is not None
    assert it_ada.locale.language_code == "it"
    assert it_ada.translation_key == ada.translation_key
    assert it_ada.bio == ada.bio  # stored until translated, but not shown (owner answer)
    assert [c.model for c in changes.changes] == ["cms.blogauthor", "cms.blogpostpage"]
    assert _titles(_html("/it/blog/author/ada-example/")) == ["Una visita"]
    it_page = _html("/it/blog/author/ada-example/")
    assert it_page.find("b", string="bowls") is None  # never the English bio in Italian


def test_an_author_of_another_language_shows_no_bio(seeded: SeedReport, ada: BlogAuthor) -> None:
    it_blog: BlogIndexPage = BlogIndexPageFactory.create(
        parent=HomePage.objects.get(locale__language_code="it")
    )
    BlogPostPageFactory.create(parent=it_blog, title="Una visita", author=ada)

    page = _html("/it/blog/author/ada-example/")
    assert page.find("b", string="bowls") is None
    assert _titles(page) == ["Una visita"]
    assert "description" not in _jsonld(page, "ProfilePage")["mainEntity"]


# --- Structured data (owner answers of 2026-11-09): Person, ProfilePage, BlogPosting


def _jsonld(page: BeautifulSoup, kind: str) -> dict[str, Any]:
    found = [
        json.loads(script.string or "")
        for script in page.find_all("script", attrs={"type": "application/ld+json"})
    ]
    matching = [data for data in found if data.get("@type") == kind]
    assert len(matching) == 1, found
    data: dict[str, Any] = matching[0]
    assert data["@context"] == "https://schema.org"
    return data


def _person_id(blog: BlogIndexPage) -> str:
    return f"{blog.get_full_url()}author/ada-example/#person"


@pytest.fixture
def profiled_ada(ada: BlogAuthor) -> BlogAuthor:
    ada.photo = MediaImageFactory.create(file__filename="ada-portrait.png")
    ada.photo_alt_text = "Ada at the wheel"
    ada.job_title = "Potter"
    ada.same_as = [("url", "https://social.example/ada"), ("url", "https://code.example/ada")]
    ada.save()
    return ada


def test_the_author_page_is_a_profile_page_of_the_person(
    blog: BlogIndexPage, profiled_ada: BlogAuthor
) -> None:
    data = _jsonld(_html("/blog/author/ada-example/"), "ProfilePage")
    author_url = f"{blog.get_full_url()}author/ada-example/"
    assert data["url"] == author_url
    assert data["inLanguage"] == "en"
    person = data["mainEntity"]
    assert person["@type"] == "Person"
    assert person["@id"] == _person_id(blog)
    assert person["name"] == "Ada Example"
    assert person["url"] == author_url
    assert person["description"] == "Ada throws bowls on the wheel."
    assert person["jobTitle"] == "Potter"
    assert person["sameAs"] == ["https://social.example/ada", "https://code.example/ada"]
    assert person["image"].startswith("http")
    assert "ada-portrait" in person["image"]


def test_an_article_points_its_author_to_the_person(
    blog: BlogIndexPage, profiled_ada: BlogAuthor
) -> None:
    post = BlogPostPageFactory.create(
        parent=blog,
        title="Spring news",
        author=profiled_ada,
        date=dt.date(2025, 6, 1),
        summary="Bowls & cups.",
    )

    data = _jsonld(_html(post.url), "BlogPosting")
    assert data["headline"] == "Spring news"
    assert data["datePublished"] == "2025-06-01"
    assert data["url"] == post.get_full_url()
    assert data["description"] == "Bowls & cups."
    assert data["author"]["@id"] == _person_id(blog)


def test_an_article_without_author_has_no_author_in_its_data(blog: BlogIndexPage) -> None:
    post = BlogPostPageFactory.create(parent=blog, title="Anonymous", author=None)
    assert "author" not in _jsonld(_html(post.url), "BlogPosting")


def test_the_person_speaks_the_page_language(blog: BlogIndexPage, profiled_ada: BlogAuthor) -> None:
    post = BlogPostPageFactory.create(parent=blog, title="Spring news", author=profiled_ada)
    translate_page(blog, ["it"])
    translate_page(post, ["it"])
    it_blog = BlogIndexPage.objects.get(translation_key=blog.translation_key, locale=_locale("it"))
    it_ada = BlogAuthor.objects.get(
        translation_key=profiled_ada.translation_key, locale=_locale("it")
    )

    untranslated = _jsonld(_html("/it/blog/author/ada-example/"), "ProfilePage")["mainEntity"]
    assert "description" not in untranslated  # the English bio is not shown in Italian
    assert "jobTitle" not in untranslated
    assert untranslated["@id"] == _person_id(it_blog)
    assert untranslated["sameAs"] == ["https://social.example/ada", "https://code.example/ada"]

    it_ada.bio = "<p>Ada modella <i>ciotole</i>.</p>"
    it_ada.job_title = "Ceramista"
    it_ada.save()
    person = _jsonld(_html("/it/blog/author/ada-example/"), "ProfilePage")["mainEntity"]
    assert person["description"] == "Ada modella ciotole."
    assert person["jobTitle"] == "Ceramista"
    it_post = BlogPostPage.objects.get(translation_key=post.translation_key, locale=_locale("it"))
    assert _jsonld(_html(it_post.url), "BlogPosting")["author"]["@id"] == _person_id(it_blog)
