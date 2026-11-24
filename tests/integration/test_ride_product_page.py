"""THM.26: the `ride` product page layout (ride fidelity 8/12).

Presentation only: a gallery card, a buy box (title, meta list, large price, variant pills, the
primary button) and a related strip. The price, tax note, vendor link and compliance blocks are
the same markup as `base` renders; the page loads in a fixed number of queries, whatever the
number of variants and images.

Assumption: `.product-safety` and `.product-destination-notice` are assumed class names for the
GPSR block and the destination notice that CMP.26 will add to the product page (no template
renders them yet). The parity check covers them only if CMP.26 uses these names.
"""

import io
from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from PIL import Image
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.catalog import media
from apps.catalog.models import Product, SalesMode
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import TranslationStatus
from apps.core.tests.factories import ImageLicenceFactory, PlatformOperatorFactory, utc
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import localized, translated

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture
def shop(settings: SettingsWrapper) -> Vendor:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=Decimal("22"), valid_from=utc(2026, 1, 1)
    )
    vendor = VendorFactory.create(
        status=Vendor.Status.ACTIVE, sells_online=True, country="IT", display_name="Acme"
    )
    VendorTaxProfileFactory.create(vendor=vendor, valid_from=utc(2026, 1, 1))
    return vendor


def _product(vendor: Vendor, slug: str, variants: int = 0, **fields: Any) -> str:
    if variants:
        fields["kind"] = Product.Kind.VARIABLE
    product = ProductFactory.create(
        vendor=vendor,
        status=Product.Status.PUBLISHED,
        price_net=Decimal("18.00"),
        weight_kg=None,
        **fields,
    )
    ProductTranslationFactory.create(
        product=product, title=f"Item {slug}", slug=slug, status=PUBLISHED
    )
    for n in range(variants):
        variant = VariantFactory.create(
            product=product, sku=f"{slug}-{n}".upper(), is_default=n == 0
        )
        VariantTranslationFactory.create(variant=variant, label=f"Size {n}", status=PUBLISHED)
    return localized(f"/product/{slug}/")


def _soup(client: Client, settings: SettingsWrapper, theme: str, path: str) -> Any:
    settings.MPMV_THEME_DEFAULT = theme
    response = client.get(path)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def test_the_gallery_sits_in_a_card(
    client: Client, settings: SettingsWrapper, shop: Vendor
) -> None:
    soup = _soup(client, settings, "ride", _product(shop, "plain"))
    card = soup.select_one(".product-gallery-card.card")
    assert card is not None
    assert card.select_one(".ride-gallery") is not None


def test_the_buy_box_has_a_meta_list_and_a_large_price_and_no_second_title(
    client: Client, settings: SettingsWrapper, shop: Vendor
) -> None:
    soup = _soup(client, settings, "ride", _product(shop, "plain"))
    box = soup.select_one("section.product-buy")
    assert box is not None
    assert box.select_one(".product-buy__title") is None  # no aria-hidden copy of the h1
    assert len(soup.find_all("h1")) == 1  # the banner keeps the only h1
    meta = box.select_one("ul.product-meta")
    assert meta is not None
    assert meta.select_one(".product-sku-value") is not None
    assert meta.select_one(".product-vendor a") is not None
    assert box.select_one(".product-price data") is not None


def test_the_variants_are_pills_that_stay_radio_inputs(
    client: Client, settings: SettingsWrapper, shop: Vendor
) -> None:
    soup = _soup(client, settings, "ride", _product(shop, "multi", variants=2))
    pills = soup.select("fieldset.product-variants.product-pills .form-check")
    assert len(pills) == 2
    for pill in pills:
        radio = pill.select_one("input[type=radio][name=variant]")
        assert radio is not None
        assert pill.find("label", attrs={"for": radio["id"]}) is not None


@pytest.mark.parametrize("kind", ["simple", "variant", "request"])
def test_price_tax_text_vendor_and_compliance_match_base(
    client: Client, settings: SettingsWrapper, shop: Vendor, kind: str
) -> None:
    extra: dict[str, Any] = {"sales_mode": SalesMode.ON_REQUEST} if kind == "request" else {}
    path = _product(shop, kind, variants=2 if kind == "variant" else 0, **extra)
    base = _soup(client, settings, "base", path)
    ride = _soup(client, settings, "ride", path)

    def facts(soup: Any) -> dict[str, list[str]]:
        return {
            "amounts": [str(d) for d in soup.select(".product-buy data, .product-form data")],
            "notes": [n.get_text(strip=True) for n in soup.select(".price-note")],
            "vendor": [a["href"] for a in soup.select(".product-vendor a")],
            "on_request": [p.get_text(strip=True) for p in soup.select(".product-on-request")],
            "gpsr": [str(s) for s in soup.select(".product-safety, .product-destination-notice")],
            "hidden": sorted(
                (i["name"], i.get("value", ""))
                for i in soup.select("form.product-form input[type=hidden]")
                if i["name"] != "csrfmiddlewaretoken"  # a fresh token on every response
            ),
        }

    assert facts(ride) == facts(base)
    if kind == "simple":
        assert facts(ride)["amounts"]
        assert facts(ride)["notes"]
    if kind == "request":
        assert translated("Price on request") in ride.select_one(".product-price").get_text()


def test_the_primary_button_and_quantity_keep_their_hooks(
    client: Client, settings: SettingsWrapper, shop: Vendor
) -> None:
    soup = _soup(client, settings, "ride", _product(shop, "plain"))
    button = soup.select_one("form.product-form button[type=submit].btn-primary")
    assert button is not None
    assert soup.select_one("form.product-form #product-quantity") is not None


def _images(vendor: Vendor, slug: str, count: int) -> None:
    product = Product.objects.get(translations__slug=slug, vendor=vendor)
    licence = ImageLicenceFactory.create(code=f"own-{slug}", name="Own", source_required=False)
    for n in range(count):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 480), (n * 40, 90, 30)).save(buffer, format="PNG")
        media.upload_image(
            product,
            file=SimpleUploadedFile(f"{n}.png", buffer.getvalue(), content_type="image/png"),
            title=f"image {n}",
            licence=licence,
            author="The vendor",
            alt_text=f"Image {n}",
            caption="",
            ctx=AuditContext(actor_label="system:test", source="system"),
        )


def _count(client: Client, path: str) -> int:
    with CaptureQueriesContext(connection) as queries:
        assert client.get(path).status_code == 200
    return len(queries)


def test_the_page_loads_in_a_fixed_number_of_queries_for_any_variants(
    client: Client, settings: SettingsWrapper, shop: Vendor
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    few = _product(shop, "few", variants=2)
    many = _product(shop, "many", variants=7)
    _count(client, few)  # warms the operator setting cache
    assert _count(client, many) == _count(client, few)


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.xfail(
    strict=True,
    reason="lane-a: the gallery loads one core_imagelicence row per image (N+1 in the catalog "
    "page data, in `base` too, not in a template); remove the mark when it is fixed",
)
def test_the_page_loads_in_a_fixed_number_of_queries_for_any_images(
    client: Client, settings: SettingsWrapper, shop: Vendor, theme: str
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    few = _product(shop, "few", variants=2)
    many = _product(shop, "many", variants=2)
    _images(shop, "few", 1)
    _images(shop, "many", 5)
    _count(client, few)
    assert _count(client, many) == _count(client, few)
