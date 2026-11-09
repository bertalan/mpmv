"""CMS.3: the blog page types, their body blocks and the publication schedule rule (spec 10 §10.2).

Done when: "Migrated article published". A language has one blog index under its home page; its
articles carry a publication date, an author (a snippet: legacy authors are not platform users)
and a body with text, images, galleries and cards to products, catalog terms and vendors, chosen
in the admin, never typed. Scheduling is Wagtail's own `go_live_at` / `expire_at`.
"""

import datetime as dt

from django.db import models
from wagtail import blocks
from wagtail.contrib.routable_page.models import RoutablePageMixin
from wagtail.fields import RichTextField
from wagtail.models import Page, TranslatableMixin
from wagtail_localize.fields import SynchronizedField, TranslatableField, get_translatable_fields

from apps.cms.blocks import (
    PageCardBlock,
    ProductCardBlock,
    ProductChooserBlock,
    TermCardBlock,
    VendorCardBlock,
)
from apps.cms.engine import (
    blog_posting_jsonld,
    person_jsonld,
    plain_text,
    profile_page_jsonld,
    schedule_problem,
)
from apps.cms.models import BlogAuthor, BlogIndexPage, BlogPostPage, HomePage

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)


def test_the_blog_index_sits_under_a_home_page_and_holds_articles() -> None:
    assert "cms.BlogIndexPage" in HomePage.subpage_types
    assert BlogIndexPage.parent_page_types == ["cms.HomePage"]
    assert BlogIndexPage.subpage_types == ["cms.BlogPostPage"]
    assert BlogIndexPage.max_count_per_parent == 1


def test_an_article_sits_under_the_blog_index_only() -> None:
    assert BlogPostPage.parent_page_types == ["cms.BlogIndexPage"]
    assert BlogPostPage.subpage_types == []


def test_an_article_is_scheduled_with_wagtail_go_live_and_expiry() -> None:
    assert issubclass(BlogPostPage, Page)
    names = {field.name for field in BlogPostPage._meta.get_fields()}
    assert {"go_live_at", "expire_at", "date", "author", "summary", "image", "body"} <= names


def test_the_author_is_a_snippet_kept_when_deleted_as_no_author() -> None:
    author = BlogPostPage._meta.get_field("author")
    assert author.related_model is BlogAuthor
    assert author.null
    assert author.remote_field.on_delete is models.SET_NULL


def test_the_article_body_offers_text_images_galleries_and_choosers() -> None:
    children = BlogPostPage._meta.get_field("body").stream_block.child_blocks
    assert isinstance(children["paragraph"], blocks.RichTextBlock)
    assert {"figure", "gallery", "cta", "faq", "featured"} <= set(children)
    cards = children["featured"].child_blocks["cards"].child_blocks
    assert isinstance(cards["page"], PageCardBlock)
    assert isinstance(cards["term"], TermCardBlock)
    assert isinstance(cards["vendor"], VendorCardBlock)
    assert isinstance(cards["product"], ProductCardBlock)
    assert isinstance(cards["product"].child_blocks["product"], ProductChooserBlock)


def test_no_schedule_is_fine() -> None:
    assert schedule_problem(None, None, NOW) is None


def test_a_future_go_live_with_a_later_expiry_is_fine() -> None:
    go_live = NOW + dt.timedelta(days=1)
    assert schedule_problem(go_live, go_live + dt.timedelta(days=30), NOW) is None


def test_the_expiry_must_follow_the_go_live() -> None:
    go_live = NOW + dt.timedelta(days=2)
    assert schedule_problem(go_live, go_live, NOW) is not None
    assert schedule_problem(go_live, go_live - dt.timedelta(hours=1), NOW) is not None


def test_an_expiry_in_the_past_is_refused() -> None:
    assert schedule_problem(None, NOW - dt.timedelta(minutes=1), NOW) is not None


# --- Authors (owner answer on PR #156: name, bio, photo and an author page; the bio translated)


def test_an_author_is_a_translatable_snippet_with_bio_photo_and_slug() -> None:
    assert issubclass(BlogAuthor, TranslatableMixin)
    names = {field.name for field in BlogAuthor._meta.get_fields()}
    assert {"name", "slug", "bio", "photo", "photo_alt_text", "locale"} <= names
    photo = BlogAuthor._meta.get_field("photo")
    assert photo.null
    assert photo.remote_field.on_delete is models.SET_NULL


def test_the_bio_is_rich_text_with_bold_italic_and_links_only() -> None:
    bio = BlogAuthor._meta.get_field("bio")
    assert isinstance(bio, RichTextField)
    assert bio.blank
    assert sorted(bio.features) == ["bold", "italic", "link"]


def test_the_bio_and_alt_text_are_translated_the_name_and_slug_are_not() -> None:
    fields = {field.field_name: field for field in get_translatable_fields(BlogAuthor)}
    assert isinstance(fields["bio"], TranslatableField)
    assert isinstance(fields["photo_alt_text"], TranslatableField)
    for synced in ("name", "slug", "photo"):
        assert isinstance(fields[synced], SynchronizedField), synced


def test_the_blog_index_serves_an_author_page() -> None:
    assert issubclass(BlogIndexPage, RoutablePageMixin)
    resolver = BlogIndexPage.get_resolver()
    assert resolver.reverse("author", "ada-example") == "author/ada-example/"


# --- Author structured data (owner answers of 2026-11-09 on PR #156)


def test_an_author_has_profile_links_and_a_translated_job_title() -> None:
    names = {field.name for field in BlogAuthor._meta.get_fields()}
    assert {"same_as", "job_title"} <= names
    fields = {field.field_name: field for field in get_translatable_fields(BlogAuthor)}
    assert isinstance(fields["same_as"], SynchronizedField)
    assert isinstance(fields["job_title"], TranslatableField)


PERSON_ID = "https://shop.example/blog/author/ada-example/#person"


def test_the_person_has_a_stable_id_and_only_the_fields_that_are_set() -> None:
    person = person_jsonld(
        person_id=PERSON_ID,
        name="Ada Example",
        url="https://shop.example/blog/author/ada-example/",
    )
    assert person == {
        "@type": "Person",
        "@id": PERSON_ID,
        "name": "Ada Example",
        "url": "https://shop.example/blog/author/ada-example/",
    }


def test_the_person_carries_image_bio_profiles_and_job_title() -> None:
    person = person_jsonld(
        person_id=PERSON_ID,
        name="Ada Example",
        url="https://shop.example/blog/author/ada-example/",
        image="https://shop.example/media/ada.png",
        description="Ada throws bowls.",
        same_as=["https://social.example/ada"],
        job_title="Potter",
    )
    assert person["image"] == "https://shop.example/media/ada.png"
    assert person["description"] == "Ada throws bowls."
    assert person["sameAs"] == ["https://social.example/ada"]
    assert person["jobTitle"] == "Potter"


def test_the_profile_page_has_the_person_as_main_entity() -> None:
    person = person_jsonld(person_id=PERSON_ID, name="Ada Example", url="https://x.example/a/")
    data = profile_page_jsonld(url="https://x.example/a/", person=person, language="it")
    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "ProfilePage"
    assert data["url"] == "https://x.example/a/"
    assert data["inLanguage"] == "it"
    assert data["mainEntity"] == person


def test_a_blog_posting_points_its_author_to_the_person_id() -> None:
    data = blog_posting_jsonld(
        url="https://x.example/blog/spring/",
        headline="Spring news",
        date_published=dt.date(2025, 6, 1),
        language="en",
        author_id=PERSON_ID,
        author_name="Ada Example",
        description="Bowls and more.",
        image="https://x.example/media/spring.png",
    )
    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "BlogPosting"
    assert data["headline"] == "Spring news"
    assert data["datePublished"] == "2025-06-01"
    assert data["inLanguage"] == "en"
    assert data["mainEntityOfPage"] == "https://x.example/blog/spring/"
    assert data["author"] == {"@type": "Person", "@id": PERSON_ID, "name": "Ada Example"}
    assert data["description"] == "Bowls and more."
    assert data["image"] == "https://x.example/media/spring.png"


def test_a_blog_posting_without_author_has_none() -> None:
    data = blog_posting_jsonld(
        url="https://x.example/b/", headline="T", date_published=dt.date(2025, 1, 1), language="en"
    )
    assert "author" not in data
    assert "image" not in data
    assert "description" not in data


def test_a_bio_becomes_plain_text_for_the_description() -> None:
    assert plain_text("<p>Ada throws <b>bowls</b> &amp; cups.</p>\n<p>Since 1990.</p>") == (
        "Ada throws bowls & cups. Since 1990."
    )
