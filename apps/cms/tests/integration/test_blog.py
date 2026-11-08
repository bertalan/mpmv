"""CMS.3: the blog, its articles, the publication schedule and the article import service.

Done when: "Migrated article published". `import_article` creates an article with the fields a
legacy article carries (title, slug, original date, author name, summary, body, schedule) under
a language's blog index and publishes it, or schedules it when its go-live is in the future. Its
dry run reports the changes and leaves the database as it was. Articles show their date and
author and the cards of the body: products, catalog terms and vendors chosen in the admin. A card
whose target is not public is left out.
"""

import datetime as dt
import json
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms.models import BlogAuthor, BlogIndexPage, BlogPostPage, HomePage
from apps.cms.services import ArticleData, SeedReport, import_article
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    BlogPostPageFactory,
)
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def blog(seeded: SeedReport) -> BlogIndexPage:
    index: BlogIndexPage = BlogIndexPageFactory.create(live=False)
    index.save_revision().publish()
    index.refresh_from_db()
    return index


@pytest.fixture
def pottery() -> Vendor:
    vendor: Vendor = VendorFactory.create(
        display_name="Pottery Studio", slug="pottery-studio", status=Vendor.Status.ACTIVE
    )
    VendorTranslationFactory.create(
        vendor=vendor, language="en", tagline="Hand-thrown bowls", status=PUBLISHED
    )
    return vendor


def _product(title: str, *, status: str = Product.Status.PUBLISHED, vendor: Vendor) -> Product:
    product: Product = ProductFactory.create(vendor=vendor, status=status)
    ProductTranslationFactory.create(
        product=product, title=title, slug=title.lower().replace(" ", "-"), status=PUBLISHED
    )
    return product


def _card(kind: str, target: Any) -> tuple[str, dict[str, Any]]:
    return (kind, {kind: target, "label": "", "text": ""})


def _legacy_article(**overrides: Any) -> ArticleData:
    fields: dict[str, Any] = {
        "title": "A visit to the workshop",
        "slug": "a-visit-to-the-workshop",
        "date": dt.date(2023, 4, 12),
        "author": "Ada Example",
        "summary": "How the bowls are made.",
        "body": [("paragraph", "<p>We spent a day at the wheel.</p>")],
    }
    fields.update(overrides)
    return ArticleData(**fields)


def _html(path: str) -> BeautifulSoup:
    response = Client().get(path)
    assert response.status_code == 200, path
    return BeautifulSoup(response.content, "html.parser")


# --- Import (Done when: "Migrated article published")


def test_a_migrated_article_is_published_with_its_date_and_author(blog: BlogIndexPage) -> None:
    changes = import_article(blog, _legacy_article())

    post = BlogPostPage.objects.get(slug="a-visit-to-the-workshop")
    assert post.live
    assert post.get_parent().pk == blog.pk
    assert post.locale_id == blog.locale_id
    assert post.date == dt.date(2023, 4, 12)
    assert post.author is not None
    assert post.author.name == "Ada Example"
    assert [(c.op, c.model) for c in changes.changes] == [
        ("create", "cms.blogauthor"),
        ("create", "cms.blogpostpage"),
    ]
    page = _html(post.url)
    assert page.h1.get_text(strip=True) == "A visit to the workshop"
    assert page.find("time", attrs={"datetime": "2023-04-12"}) is not None
    assert "Ada Example" in page.get_text()
    assert "We spent a day at the wheel." in page.get_text()


def test_the_dry_run_reports_the_article_and_changes_nothing(blog: BlogIndexPage) -> None:
    changes = import_article(blog, _legacy_article(), dry_run=True)

    article = changes.changes[-1]
    assert (article.op, article.model) == ("create", "cms.blogpostpage")
    assert article.key == "en:a-visit-to-the-workshop"
    assert article.after is not None
    assert article.after["title"] == "A visit to the workshop"
    assert article.after["date"] == "2023-04-12"
    assert article.after["author"] == "Ada Example"
    assert article.after["live"] is True
    assert not BlogPostPage.objects.exists()
    assert not BlogAuthor.objects.exists()


def test_an_existing_author_is_reused(blog: BlogIndexPage) -> None:
    author = BlogAuthorFactory.create(name="Ada Example")
    changes = import_article(blog, _legacy_article())

    assert [c.model for c in changes.changes] == ["cms.blogpostpage"]
    assert BlogPostPage.objects.get().author == author
    assert BlogAuthor.objects.count() == 1


def test_an_article_without_author_is_published_without_byline(blog: BlogIndexPage) -> None:
    import_article(blog, _legacy_article(author=""))

    post = BlogPostPage.objects.get()
    assert post.author is None
    assert _html(post.url).find(class_="article-author") is None


def test_an_article_already_imported_is_left_alone(blog: BlogIndexPage) -> None:
    import_article(blog, _legacy_article())
    again = import_article(blog, _legacy_article(title="Another title"))

    assert again.is_empty
    assert BlogPostPage.objects.get().title == "A visit to the workshop"


def test_an_article_goes_under_the_blog_of_its_language(blog: BlogIndexPage) -> None:
    it_home = HomePage.objects.get(locale__language_code="it")
    it_blog: BlogIndexPage = BlogIndexPageFactory.create(parent=it_home)
    import_article(it_blog, _legacy_article(slug="una-visita", title="Una visita"))

    post = BlogPostPage.objects.get()
    assert post.locale.language_code == "it"
    assert post.url == "/it/blog/una-visita/"


def test_a_schedule_with_the_expiry_before_the_go_live_is_refused(blog: BlogIndexPage) -> None:
    go_live = timezone.now() + dt.timedelta(days=2)
    with pytest.raises(ValidationError):
        import_article(blog, _legacy_article(go_live_at=go_live, expire_at=go_live))
    assert not BlogPostPage.objects.exists()


# --- Scheduling (Wagtail's go_live_at / expire_at)


def test_an_article_with_a_future_go_live_is_scheduled_not_live(blog: BlogIndexPage) -> None:
    go_live = timezone.now() + dt.timedelta(days=3)
    changes = import_article(blog, _legacy_article(go_live_at=go_live))

    post = BlogPostPage.objects.get()
    assert not post.live
    assert post.get_latest_revision().approved_go_live_at == go_live
    assert changes.changes[-1].after is not None
    assert changes.changes[-1].after["live"] is False
    assert Client().get("/blog/a-visit-to-the-workshop/").status_code == 404
    assert "A visit to the workshop" not in _html(blog.url).get_text()


def test_a_scheduled_article_goes_live_at_its_time(blog: BlogIndexPage) -> None:
    import_article(blog, _legacy_article(go_live_at=timezone.now() + dt.timedelta(days=3)))
    post = BlogPostPage.objects.get()
    past = timezone.now() - dt.timedelta(minutes=1)
    post.get_latest_revision().__class__.objects.filter(pk=post.get_latest_revision().pk).update(
        approved_go_live_at=past
    )

    call_command("publish_scheduled")

    post.refresh_from_db()
    assert post.live
    assert "A visit to the workshop" in _html(blog.url).get_text()


def test_an_article_leaves_the_site_at_its_expiry(blog: BlogIndexPage) -> None:
    import_article(blog, _legacy_article(expire_at=timezone.now() + dt.timedelta(days=30)))
    BlogPostPage.objects.update(expire_at=timezone.now() - dt.timedelta(minutes=1))

    call_command("publish_scheduled")

    post = BlogPostPage.objects.get()
    assert not post.live
    assert post.expired
    assert "A visit to the workshop" not in _html(blog.url).get_text()


# --- Index


def test_the_index_lists_live_articles_newest_first(blog: BlogIndexPage) -> None:
    BlogPostPageFactory.create(parent=blog, title="Older", date=dt.date(2024, 1, 5))
    BlogPostPageFactory.create(parent=blog, title="Newer", date=dt.date(2025, 6, 1))
    BlogPostPageFactory.create(parent=blog, title="Draft", live=False)

    page = _html(blog.url)
    titles = [h.get_text(strip=True) for h in page.select("article.post-summary h2")]
    assert titles == ["Newer", "Older"]
    assert page.find("time", attrs={"datetime": "2025-06-01"}) is not None


def test_the_index_is_paginated(blog: BlogIndexPage) -> None:
    for day in range(1, 15):
        BlogPostPageFactory.create(parent=blog, title=f"Day {day}", date=dt.date(2025, 1, day))

    first = _html(blog.url)
    second = _html(f"{blog.url}?page=2")
    assert len(first.select("article.post-summary")) == 12
    assert len(second.select("article.post-summary")) == 2
    assert first.find("a", attrs={"rel": "next"}) is not None
    assert Client().get(f"{blog.url}?page=99").status_code == 404


# --- Cards to products, terms and vendors


def test_the_body_cards_link_products_and_vendors_and_skip_the_hidden_ones(
    blog: BlogIndexPage, pottery: Vendor
) -> None:
    listed = _product("Blue bowl", vendor=pottery)
    draft = _product("Secret jug", vendor=pottery, status=Product.Status.DRAFT)
    body = [
        (
            "featured",
            {
                "heading": "In this article",
                "cards": [
                    _card("product", listed),
                    _card("product", draft),
                    _card("vendor", pottery),
                ],
            },
        )
    ]
    import_article(blog, _legacy_article(body=body))

    page = _html(BlogPostPage.objects.get().url)
    links = {a.get_text(strip=True): a["href"] for a in page.select("section.block-featured a")}
    assert links["Blue bowl"] == "/product/blue-bowl/"
    assert links["Pottery Studio"] == "/shop/pottery-studio/"
    assert "Secret jug" not in links


def test_an_article_image_needs_alt_text(blog: BlogIndexPage) -> None:
    from apps.core.tests.factories import MediaImageFactory

    with pytest.raises(ValidationError):
        BlogPostPageFactory.create(parent=blog, image=MediaImageFactory.create())


# --- Admin


@pytest.fixture
def editor() -> User:
    user: User = UserFactory.create(superuser=True)
    return user


def test_the_editor_adds_an_article_with_author_schedule_and_cards(
    client: Client, blog: BlogIndexPage, editor: User
) -> None:
    verified_login(client, editor)
    response = client.get(reverse("wagtailadmin_pages:add", args=("cms", "blogpostpage", blog.pk)))
    assert response.status_code == 200
    html = response.content.decode()
    for marker in ("go_live_at", "expire_at", 'name="author"', "Product", "Term", "Vendor"):
        assert marker in html, marker


def test_the_product_chooser_lists_only_listed_products(
    client: Client, seeded: SeedReport, editor: User, pottery: Vendor
) -> None:
    listed = _product("Blue bowl", vendor=pottery)
    draft = _product("Secret jug", vendor=pottery, status=Product.Status.DRAFT)
    verified_login(client, editor)

    response = client.get(reverse("cms_product_chooser:choose"))
    assert response.status_code == 200
    html = str(json.loads(response.content)["html"])
    assert listed.sku in html
    assert draft.sku not in html
    chosen = client.get(reverse("cms_product_chooser:chosen", args=(draft.pk,)))
    assert chosen.status_code == 404
