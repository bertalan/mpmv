"""CMP.8 follow-up (#1097): a variant's cover image shows its AI label in the variant chips.

The chip thumbnail is the variant's cover `ProductImage`, which already carries `ai_origin`; the
page carries it through `VariantChoice` and renders the shared `components/ai_image_label.html`,
in both themes, with no extra query per variant.
"""

import io
import re
import secrets

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from PIL import Image
from wagtail.models import Site

from apps.catalog import ai_labels, media
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
    VariantTranslationFactory,
)
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.tests.factories import ImageLicenceFactory
from apps.themes.services import use_theme
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import product_url

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]
CTX = AuditContext(actor_label="system:test", source="system")


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    django_apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _png(color: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return SimpleUploadedFile(f"{color}.png", buffer.getvalue(), content_type="image/png")


def _page(origins: tuple[str, ...]) -> str:
    """A variable product with one variant per origin, each with its own cover image."""
    product = ProductFactory.create(
        status="published",
        kind="variable",
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True),
    )
    slug = f"mug-{secrets.token_hex(3)}"
    ProductTranslationFactory.create(
        product=product, language=primary_language(), title="Mug", slug=slug, status="published"
    )
    licence = ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    for number, origin in enumerate(origins):
        media.upload_image(
            product,
            file=_png(("red", "green", "blue")[number]),
            title=f"v{number}",
            licence=licence,
            author="The vendor",
            alt_text=f"Mug {number}",
            ai_origin=origin,
            ctx=CTX,
        )
        row = product.images.get(image__title=f"v{number}")
        variant = VariantFactory.create(
            product=product, sku=f"MUG-{number}", position=number, cover_image=row
        )
        VariantTranslationFactory.create(
            variant=variant, language=primary_language(), label=f"V{number}", status="published"
        )
    return product_url(primary_language(), slug)


def _chips(html: str) -> list[str]:
    picker = re.search(r"<fieldset class=\"product-variants.*?</fieldset>", html, re.S)
    assert picker is not None
    return re.findall(r"<li.*?</li>", picker.group(0), re.S)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_variant_chip_labels_its_ai_image_and_only_that_one(client: Client, theme: str) -> None:
    url = _page((ai_labels.AI_GENERATED, ai_labels.AI_EDITED, ai_labels.AI_NONE))
    with use_theme(theme):
        html = client.get(url).content.decode()
    generated, edited, plain = _chips(html)
    assert 'data-ai-origin="generated"' in generated
    assert "AI-generated image" in generated
    assert "trainedAlgorithmicMedia" in generated
    assert 'data-ai-origin="edited"' in edited
    assert "AI-edited image" in edited
    assert "data-ai-origin" not in plain
    assert "AI-" not in plain


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_chip_label_is_outside_the_radio_label(client: Client, theme: str) -> None:
    url = _page((ai_labels.AI_GENERATED,))
    with use_theme(theme):
        html = client.get(url).content.decode()
    (chip,) = _chips(html)
    inside = re.search(r"<label.*?</label>", chip, re.S)
    assert inside is not None
    assert "gallery__ai-label" not in inside.group(0)  # a paragraph never sits in a label
    assert "gallery__ai-label" in chip


def _queries(client: Client, url: str) -> int:
    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200
    return len(captured)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_label_adds_no_query_per_image(client: Client, theme: str) -> None:
    plain = _page((ai_labels.AI_NONE,) * 3)
    labelled = _page((ai_labels.AI_GENERATED, ai_labels.AI_EDITED, ai_labels.AI_GENERATED))
    with use_theme(theme):
        client.get(plain)  # warm the caches
        client.get(labelled)
        assert _queries(client, labelled) == _queries(client, plain)  # the label costs none
