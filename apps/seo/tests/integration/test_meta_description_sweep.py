"""SEO.17: a sweep over every public Wagtail page type. A page whose search description is set
prints it as its meta description, under each theme. A new page type fails here until it gets a
builder below, or an exemption that says why it has no description of its own."""

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from wagtail.models import Locale, Page, get_page_models

from apps.cms.models import HomePage
from apps.cms.tests import factories as cms
from apps.events.models import EventIndexPage
from apps.events.tests import factories as events

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]

META = re.compile(r'<meta name="description" content="([^"]*)">')
TEXT = "A short text for search engines."


def _published(page: Page) -> Page:
    page.search_description = TEXT
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _home() -> Page:
    return _published(HomePage.objects.live().first())


def _event() -> Page:
    soon = dt.datetime.now(dt.UTC) + dt.timedelta(days=10)
    return _published(
        events.EventPageFactory.create(starts_at=soon, ends_at=soon + dt.timedelta(hours=3))
    )


def _blog_post() -> Page:
    blog = _published(cms.BlogIndexPageFactory.create())
    return _published(cms.BlogPostPageFactory.create(parent=blog))


BUILDERS: dict[str, Callable[[], Page]] = {
    "HomePage": _home,
    "StandardPage": lambda: _published(cms.StandardPageFactory.create()),
    "BlogIndexPage": lambda: _published(cms.BlogIndexPageFactory.create()),
    "BlogPostPage": _blog_post,
    "EventIndexPage": lambda: _published(
        EventIndexPage.objects.live().get(locale=Locale.get_default())  # made by a migration
    ),
    "EventPage": _event,
    "FormPage": lambda: _published(cms.FormPageFactory.create()),
    "VendorRecruitmentPage": lambda: _published(cms.VendorRecruitmentPageFactory.create()),
    "LegalPage": lambda: _published(cms.LegalPageFactory.create()),
}
EXEMPT = {
    "TermLandingPage": (
        "no URL of its own: it renders on its term's listing, whose description is tested in "
        "apps/catalog/tests/integration/test_listing_meta_description.py"
    ),
    "VendorLandingPage": (
        "no URL of its own: it renders on the vendor storefront listing, tested with the listings"
    ),
}


def test_every_public_page_type_is_swept_or_exempt() -> None:
    project = {
        model.__name__
        for model in get_page_models()
        if model.__module__.startswith("apps.") and not model._meta.abstract
    }
    assert project - BUILDERS.keys() - EXEMPT.keys() == set()
    assert all(reason.strip() for reason in EXEMPT.values())


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("kind", sorted(BUILDERS))
def test_the_search_description_is_the_meta_description(
    kind: str, theme: str, settings: Any
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    page = BUILDERS[kind]()
    response = Client().get(page.url)
    assert response.status_code == 200, kind
    assert META.findall(response.content.decode()) == [TEXT], kind
