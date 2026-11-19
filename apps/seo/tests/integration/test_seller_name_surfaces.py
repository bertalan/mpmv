"""SEO.15 follow-up: a natural person's legal name is never shown on a public marketing surface.

The vendor below is a sole trader whose only name is their own legal name. The product page,
the storefront heading, the listing filter, the "other sellers" block and the
event surfaces (page, feeds) must not print it; a vendor with a shop name keeps it. Checkout,
orders and legal pages show the legal identity on purpose and are not covered here.
"""

import datetime as dt

import pytest
from django.apps import apps
from django.test import Client, RequestFactory
from django.utils import timezone
from wagtail.models import Site

from apps.catalog import listings, services
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.cms import services as cms_services
from apps.cms.blocks import _vendor_targets
from apps.cms.services import seed_site
from apps.cms.tests.factories import VendorLandingPageFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import product_url, vendor_url

pytestmark = pytest.mark.django_db

PERSON = "Zorro Quillfeather"


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _person() -> Vendor:
    return VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        sells_online=True,
        display_name=PERSON,
        legal_name=PERSON,
        first_name="Zorro",
        last_name="Quillfeather",
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
    )


def _product(vendor: Vendor, slug: str | None = None, **kwargs: object) -> Product:
    product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED, **kwargs)
    ProductTranslationFactory.create(
        product=product,
        language=primary_language(),
        title="Quill mug",
        slug=f"quill-mug-{product.pk}" if slug is None else slug,
        status=TranslationStatus.PUBLISHED,
    )
    return product


def _get(path: str) -> str:
    response = Client().get(path)
    assert response.status_code == 200, path
    return response.content.decode()


def test_the_product_page_does_not_name_the_person() -> None:
    _product(_person(), "quill-mug")
    html = _get(product_url(primary_language(), "quill-mug"))
    assert PERSON not in html
    assert "product-vendor" not in html


def test_the_product_page_names_a_shop() -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Quill Shop")
    _product(vendor, "quill-mug")
    assert "Quill Shop" in _get(product_url(primary_language(), "quill-mug"))


def test_the_storefront_heading_and_title_do_not_name_the_person() -> None:
    vendor = _person()
    _product(vendor)
    html = _get(vendor_url(primary_language(), vendor.slug))
    assert PERSON not in html
    anchor = services.scope_anchor(services.ListingScope(vendor=vendor), primary_language())
    assert anchor is not None
    assert anchor.name == ""
    assert services.listing_names([services.ListingScope(vendor=vendor)], primary_language()) == [
        anchor.name
    ]


def test_the_vendor_filter_does_not_offer_the_person() -> None:
    person = _person()
    _product(person)
    scope = services.ListingScope()
    labels = [o.label for o in services.listing_vendor_options(scope, listings.ListingParams())]
    assert PERSON not in labels


def test_event_surfaces_do_not_name_the_person() -> None:
    start = (timezone.now() + dt.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(
        vendor=_person(), title="Quill fair", starts_at=start, ends_at=start + dt.timedelta(hours=8)
    )
    page.save_revision().publish()
    page.refresh_from_db()
    assert page.vendor is not None
    assert PERSON not in _get(page.url)
    base = str(primary_index().url)
    for path in (f"vendor/{page.vendor.slug}/feed.rss", f"vendor/{page.vendor.slug}/calendar.ics"):
        assert PERSON not in _get(base + path)


def test_the_cms_card_and_the_story_link_leave_out_a_person() -> None:
    person = _person()
    shop = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Quill Shop")
    assert set(_vendor_targets([person, shop], primary_language())) == {shop.pk}
    for vendor in (person, shop):
        VendorLandingPageFactory.create(
            vendor=vendor, title=f"Story {vendor.pk}"
        ).save_revision().publish()
    story = cms_services._vendor_story(RequestFactory().get("/"), person, primary_language())
    assert story is None
    shown = cms_services._vendor_story(RequestFactory().get("/"), shop, primary_language())
    assert shown is not None
    assert "Quill Shop" in shown.before


def test_a_sole_trader_shop_name_is_shown_on_events_and_feeds() -> None:
    start = (timezone.now() + dt.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE,
        display_name="Quill Ceramics",
        legal_name=PERSON,
        first_name="Zorro",
        last_name="Quillfeather",
        legal_form=Vendor.LegalForm.SOLE_PROPRIETORSHIP,
    )
    page = EventPageFactory.create(
        vendor=vendor, title="Quill fair", starts_at=start, ends_at=start + dt.timedelta(hours=8)
    )
    page.save_revision().publish()
    page.refresh_from_db()
    assert "Quill Ceramics" in _get(page.url)
    feed = _get(str(primary_index().url) + f"vendor/{vendor.slug}/feed.rss")
    assert "Quill Ceramics" in feed
    assert PERSON not in feed


def test_the_site_links_list_leaves_out_a_person() -> None:
    from apps.seo import site_links

    _person()
    VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Quill Shop")
    labels = [link.label for link in site_links._shops(primary_language()).links]
    assert labels == ["Quill Shop"]
