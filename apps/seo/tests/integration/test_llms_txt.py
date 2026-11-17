"""SEO.2: the generated `llms.txt` (spec 10): a site summary and the main public sections per
language, built from the same data as the sitemaps, on the site's root URL, never the Host."""

import re
from urllib.parse import urlsplit

import pytest
from django.apps import apps
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse
from wagtail.models import Site

from apps.catalog import slugs
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.cms.tests.factories import StandardPageFactory, TermLandingPageFactory
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
PRIMARY = settings.LANGUAGE_CODE
HOSTILE = "evil.example"


@pytest.fixture(autouse=True)
def _indexing_on() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def other_language() -> str:
    return next(code for code in configured_languages() if code != PRIMARY)


def links(text: str) -> list[tuple[str, str]]:
    return re.findall(r"^- \[([^\]]+)\]\(([^)]+)\)", text, flags=re.MULTILINE)


def test_llms_txt_is_markdown_text_with_title_and_sections(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Bottega Uno")
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language=PRIMARY, status=PUBLISHED, slug="cat-llms", name="Mugs llms"
    )
    item = ProductFactory.create(vendor=vendor, category=category, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=item, language=PRIMARY, slug=f"item-{item.pk}", status=PUBLISHED
    )
    page = StandardPageFactory.create(title="About us llms")
    page.save_revision().publish()
    response = client.get(reverse("llms_txt"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    text = response.content.decode()
    assert text.startswith("# ")
    assert "\n## " in text
    root = settings.WAGTAILADMIN_BASE_URL.rstrip("/")
    found = dict(links(text))
    assert found["Bottega Uno"] == root + slugs.vendor_path(
        PRIMARY, vendor.slug, default_language=PRIMARY
    )
    assert found["Mugs llms"] == root + slugs.category_path(
        PRIMARY, ["cat-llms"], default_language=PRIMARY
    )
    assert found["About us llms"] == root + urlsplit(page.get_url()).path
    assert all(url.startswith(root + "/") for url in found.values())


@override_settings(ALLOWED_HOSTS=["*"])
def test_llms_txt_never_takes_the_host_header(client: Client) -> None:
    StandardPageFactory.create().save_revision().publish()
    text = client.get(reverse("llms_txt"), HTTP_HOST=HOSTILE).content.decode()
    assert HOSTILE not in text
    assert settings.WAGTAILADMIN_BASE_URL.rstrip("/") in text


def test_each_language_has_its_own_file_with_its_own_pages_only(client: Client) -> None:
    other = other_language()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Only primary shop")
    item = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(product=item, language=PRIMARY, status=PUBLISHED)
    in_primary = client.get(reverse("llms_language", kwargs={"language": PRIMARY}))
    assert in_primary.status_code == 200
    assert "Only primary shop" in in_primary.content.decode()
    # The vendor page exists only in the primary language: no fallback copy under `other`.
    in_other = client.get(reverse("llms_language", kwargs={"language": other}))
    assert in_other.status_code == 200
    assert "Only primary shop" not in in_other.content.decode()
    assert client.get(reverse("llms_txt")).content == in_primary.content


def test_llms_txt_leaves_out_noindex_drafts_and_inactive_vendors(client: Client) -> None:
    TermLandingPageFactory.create(title="Landing hidden").save_revision().publish()
    StandardPageFactory.create(title="Unfinished llms", live=False)
    suspended = VendorFactory.create(status=Vendor.Status.SUSPENDED, display_name="Suspended shop")
    item = ProductFactory.create(vendor=suspended, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(product=item, language=PRIMARY, status=PUBLISHED)
    text = client.get(reverse("llms_txt")).content.decode()
    for word in ("Landing hidden", "Unfinished llms", "Suspended shop"):
        assert word not in text


def test_llms_txt_is_private_cached_and_unknown_language_is_404(client: Client) -> None:
    cache = client.get(reverse("llms_txt")).get("Cache-Control", "")
    assert "private" in cache
    assert "max-age" in cache
    missing = client.get(reverse("llms_language", kwargs={"language": "xx"}))
    assert missing.status_code == 404
    assert "max-age" not in missing.get("Cache-Control", "")


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_with_indexing_off_there_is_no_llms_txt(client: Client) -> None:
    apps.get_model("seo", "SeoSettings").objects.update(allow_indexing=False)
    assert client.get(reverse("llms_txt")).status_code == 404


@override_settings(FORCE_NOINDEX=True)
def test_a_forced_noindex_environment_has_no_llms_txt(client: Client) -> None:
    assert client.get(reverse("llms_txt")).status_code == 404
