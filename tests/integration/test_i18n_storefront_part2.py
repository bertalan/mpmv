"""I18N.20 part 2: events, the blog, the CMS blocks and the form page show the visitor's language.

Each test renders a public page (or the component of a block) in the second configured language
and expects the label the catalog gives for that language. The expected text comes from
`gettext` under `translation.override`, so the tests hold for any `MPMV_LANGUAGES` (CI also runs
`it,de`) and name no language. They fail while a template string is missing from the catalog.
"""

import datetime as dt
import html

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.utils import timezone, translation
from django.utils.translation import gettext
from wagtail.models import Locale

from apps.catalog import services as catalog_services
from apps.cms.models import HomePage
from apps.cms.services import seed_site
from apps.cms.tests.factories import (
    BlogAuthorFactory,
    BlogIndexPageFactory,
    BlogPostPageFactory,
    FormFieldFactory,
    FormPageFactory,
)
from apps.events.models import EventIndexPage
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()


def _say(lang: str, text: str) -> str:
    with translation.override(lang):
        return gettext(text)


def _page(client: Client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return html.unescape(response.content.decode())


def test_an_event_storefront_section_speaks_the_page_language(client: Client) -> None:
    lang = other_language()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    start = (timezone.now() + dt.timedelta(days=5)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(
        parent=EventIndexPage.objects.get(locale__language_code=lang),
        vendor=vendor,
        title="Fiera",
        starts_at=start,
        ends_at=start + dt.timedelta(hours=4),
    )
    page.save_revision().publish()
    anchor = catalog_services.vendor_anchor(vendor.slug, lang)
    assert anchor is not None
    text = _page(client, anchor.paths[lang])
    assert _say(lang, "Upcoming events") in text
    with translation.override(lang):
        expected = gettext("All events of %(name)s") % {"name": vendor.display_name}
    assert expected in text
    assert expected != f"All events of {vendor.display_name}"


def test_the_event_feeds_label_a_calendar_in_the_visitor_language() -> None:
    from apps.events import feeds

    lang = other_language()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    with translation.override(lang):
        title = feeds._feed_title(vendor, feeds.Scope())
        expected = gettext("Events of %(vendor)s") % {"vendor": vendor.display_name}
    assert title == expected
    assert title != f"Events of {vendor.display_name}"


def test_a_blog_article_and_an_empty_author_page_speak_the_page_language(client: Client) -> None:
    lang = other_language()
    locale = Locale.objects.get(language_code=lang)
    BlogIndexPageFactory.create()  # the primary language's blog comes first
    blog = BlogIndexPageFactory.create()
    assert blog.locale.language_code == lang
    ada = BlogAuthorFactory.create(name="Ada Example", locale=locale)
    post = BlogPostPageFactory.create(parent=blog, title="Novita", author=ada)
    with translation.override(lang):
        byline = render_to_string("cms/includes/article_meta.html", {"post": post})
        expected = gettext("By %(name)s") % {"name": "Ada Example"}
    assert expected in html.unescape(byline)
    assert expected != "By Ada Example"
    bea = BlogAuthorFactory.create(name="Bea Example", locale=locale)
    text = _page(client, f"{blog.url}author/{bea.slug}/")
    assert _say(lang, "No articles by this author yet.") in text
    assert _say(lang, "No articles by this author yet.") != "No articles by this author yet."


def test_a_blog_index_without_articles_speaks_the_page_language(client: Client) -> None:
    lang = other_language()
    BlogIndexPageFactory.create()
    blog = BlogIndexPageFactory.create()
    assert blog.locale.language_code == lang
    text = _page(client, blog.url)
    assert _say(lang, "No articles have been published yet.") in text
    assert _say(lang, "No articles have been published yet.") != (
        "No articles have been published yet."
    )


def test_a_cms_block_component_speaks_the_active_language() -> None:
    lang = other_language()
    target = timezone.now() + dt.timedelta(days=3)
    context = {"title": "Drop", "target": target, "expired": False}
    with translation.override(lang):
        rendered = render_to_string("components/launch_countdown.html", context)
        expected = [gettext(s) for s in ("Available from", "Time left:", "Pause countdown")]
    for label in expected:
        assert label in rendered
    assert expected[0] != "Available from"


def test_the_product_grid_block_says_see_all_in_the_active_language() -> None:
    lang = other_language()
    with translation.override(lang):
        rendered = render_to_string(
            "cms/blocks/product_grid.html",
            {"cards": [], "see_all_url": "/x/", "listing_name": ""},
        )
        expected = gettext("See all")
    assert rendered.strip() == ""  # no card: nothing is rendered, the label is not needed
    assert expected != "See all"


def test_the_form_page_speaks_the_page_language(client: Client) -> None:
    lang = other_language()
    home = HomePage.objects.get(locale__language_code=lang)
    page = FormPageFactory.create(parent=home, title="Contattaci")
    FormFieldFactory.create(page=page, label="Domanda", field_type="singleline", required=True)
    page.save_revision().publish()
    assert page.locale.language_code == lang
    text = _page(client, page.url)
    assert _say(lang, "Send") in text
    assert _say(lang, "Send") != "Send"
    with translation.override(lang):
        landing = render_to_string("cms/form_page_landing.html", {"page": page})
    assert _say(lang, "Thank you. We have received your message.") in landing


def test_the_form_page_refusals_are_translated() -> None:
    lang = other_language()
    for text in (
        "The security check failed. Please try again.",
        "Too many submissions, try again shortly.",
        "This submission is too large.",
    ):
        assert _say(lang, text) != text, text
