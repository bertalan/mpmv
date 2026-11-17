"""I18N.16 and the SEO.2 review: the sitemaps and `llms.txt` follow a status change at the next
request (a version bumped by the services), not after the five-minute cache."""

import pytest
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail.models import Site

from apps.catalog import slugs
from apps.catalog.models import Product
from apps.catalog.services import delete_product, save_product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, TranslationStatus
from apps.core.sitemap_version import sitemap_version
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.services import change_status
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRIMARY = settings.LANGUAGE_CODE
CTX = AuditContext(actor_label="test", source=AuditLog.Source.COMMAND)


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    cache.clear()
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def listed_product() -> tuple[Product, str]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    item = ProductFactory.create(status=Product.Status.PUBLISHED, vendor=vendor)
    ProductTranslationFactory.create(
        product=item, language=PRIMARY, slug=f"fresh-{item.pk}", status=TranslationStatus.PUBLISHED
    )
    return item, slugs.product_path(PRIMARY, f"fresh-{item.pk}", default_language=PRIMARY)


def in_sitemap(client: Client, path: str) -> bool:
    response = client.get(
        reverse("sitemap_section", kwargs={"language": PRIMARY, "kind": "products"})
    )
    assert response.status_code == 200
    return path in response.content.decode()


def in_llms(client: Client, path: str) -> bool:
    return path in client.get(reverse("llms_txt")).content.decode()


def test_suspending_a_vendor_removes_its_products_at_once_and_reinstating_restores(
    client: Client,
) -> None:
    item, path = listed_product()
    assert in_sitemap(client, path)  # primes both caches
    assert in_llms(client, path)
    change_status(item.vendor, engine.SUSPENDED, ctx=CTX)
    assert not in_sitemap(client, path)
    assert not in_llms(client, path)
    change_status(item.vendor, engine.ACTIVE, ctx=CTX)
    assert in_sitemap(client, path)
    assert in_llms(client, path)


def test_closing_a_vendor_removes_its_products_at_once(client: Client) -> None:
    item, path = listed_product()
    assert in_sitemap(client, path)
    change_status(item.vendor, engine.CLOSED, ctx=CTX)
    assert not in_sitemap(client, path)


def test_a_retired_product_leaves_the_sitemap_at_once(client: Client) -> None:
    item, path = listed_product()
    assert in_sitemap(client, path)
    assert in_llms(client, path)
    item.status = Product.Status.RETIRED
    save_product(item, ctx=CTX)
    assert not in_sitemap(client, path)
    assert not in_llms(client, path)


def test_a_deleted_product_leaves_the_sitemap_at_once(client: Client) -> None:
    item, path = listed_product()
    assert in_sitemap(client, path)
    delete_product(item, ctx=CTX)
    assert not in_sitemap(client, path)


def test_a_dry_run_does_not_bump_the_version() -> None:
    item, _path = listed_product()
    before = sitemap_version()
    change_status(item.vendor, engine.SUSPENDED, ctx=CTX, dry_run=True)
    assert sitemap_version() == before


def test_a_language_change_bumps_the_version() -> None:
    from apps.core.language_services import change_languages

    before = sitemap_version()
    target = [PRIMARY, "de" if PRIMARY != "de" else "fr"]
    change_languages(target, ctx=CTX, dry_run=False)
    assert sitemap_version() > before


def test_llms_txt_is_cached_and_resolves_titles_in_a_fixed_number_of_queries(
    client: Client,
) -> None:
    listed_product()
    url = reverse("llms_txt")
    client.get(url)  # primes the cache
    with CaptureQueriesContext(connection) as warm:
        client.get(url)
    cache.clear()
    with CaptureQueriesContext(connection) as cold:
        client.get(url)
    assert len(warm) < len(cold)
    for _ in range(4):
        listed_product()
    cache.clear()
    with CaptureQueriesContext(connection) as more:
        client.get(url)
    assert len(more) == len(cold)
