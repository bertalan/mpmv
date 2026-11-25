"""ORD.40: cart and checkout lines show a thumbnail of the product (presentation only).

Done-when: a line with a variant image, one with only a product image, one whose language has a
dedicated image and one with none; the thumbnail is the cover of the gallery the language shows
(spec 03 §3.2); the alt rule; the query count does not grow per line; the ride and base themes
both render it.
"""

import re
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from apps.cart.models import Cart
from apps.cart.tests.factories import CartLineFactory
from apps.catalog import product_pages
from apps.catalog.models import Product, ProductImage, Variant
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductImageFactory,
    ProductImageTranslationFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.core.tests.factories import MediaImageFactory
from apps.orders.tests.integration.test_checkout_views import (
    sign_off_details,
    tax_setup,
    vendor,
    with_cart,
)
from apps.orders.ui import present
from tests.i18n import other_language

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface", "tax_setup")]
__all__ = ["tax_setup"]
D = Decimal


def product_for(owner: Any, title: str, *, variable: bool = False) -> Product:
    product = ProductFactory.create(
        vendor=owner,
        status=Product.Status.PUBLISHED,
        price_net=D("20.00"),
        stock_qty=50,
        weight_kg=D("0.500"),
        kind=Product.Kind.VARIABLE if variable else Product.Kind.SIMPLE,
    )
    ProductTranslationFactory.create(product=product, title=title, status="published")
    return product


def put_line(cart: Cart, product: Product, variant: Variant | None = None) -> None:
    CartLineFactory.create(
        cart=cart, product=product, variant=variant, quantity=1, added_price_net=D("20.00")
    )


def picture(
    product: Product,
    name: str,
    *,
    variant: Variant | None = None,
    language: str | None = None,
    is_cover: bool = False,
    alt: str = "",
) -> ProductImage:
    row = ProductImageFactory.create(
        product=product,
        variant=variant,
        language=language,
        is_cover=is_cover,
        image=MediaImageFactory.create(file__filename=f"{name}.png"),
    )
    if alt:
        ProductImageTranslationFactory.create(product_image=row, language="en", alt_text=alt)
    return row


def imgs(html: str, name: str) -> list[str]:
    """The `<img>` tags of a cart line whose source is the file `name`."""
    return [tag for tag in re.findall(r"<img[^>]*>", html) if f"{name}" in tag]


def cart_html(client: Client, settings: Any, theme: str) -> str:
    settings.MPMV_THEME_DEFAULT = theme
    return client.get(reverse("orders:cart")).content.decode()


def thumbnails(pairs: list[tuple[int, int | None]], language: str = "en") -> Any:
    return product_pages.line_thumbnails(pairs, language)


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_line_shows_the_variant_image_the_product_image_or_nothing(
    client: Client, settings: Any, theme: str
) -> None:
    cart = with_cart(client)
    owner = vendor()
    variable = product_for(owner, "Helmet", variable=True)
    red = VariantFactory.create(product=variable, sku="HEL-RED")
    own = picture(variable, "redhelmet", variant=red, is_cover=True)
    picture(variable, "helmetshared", is_cover=True)
    plain = product_for(owner, "Gloves")
    picture(plain, "gloves", is_cover=True)
    bare = product_for(owner, "Socks")
    put_line(cart, variable, red)
    put_line(cart, plain)
    put_line(cart, bare)
    assert own.variant_id == red.pk
    html = cart_html(client, settings, theme)
    assert len(imgs(html, "redhelmet")) == 1
    assert not imgs(html, "helmetshared")  # the variant's own gallery wins
    assert len(imgs(html, "gloves")) == 1
    assert "width=" in imgs(html, "gloves")[0]  # the size is set: no layout shift
    assert "height=" in imgs(html, "gloves")[0]
    # the line without an image shows no `<img>` of the product (a placeholder box in base)
    assert html.count("cart-line__name") == 3
    assert len([tag for tag in re.findall(r"<img[^>]*>", html) if ".png" in tag]) == 2


def test_a_variant_without_images_shows_the_product_cover() -> None:
    owner = vendor()
    product = product_for(owner, "Helmet", variable=True)
    blue = VariantFactory.create(product=product, sku="HEL-BLUE")
    picture(product, "helmetshared", is_cover=True)
    found = thumbnails([(product.pk, blue.pk)])
    assert "helmetshared" in found[(product.pk, blue.pk)].image.file.name


def test_a_variant_cover_is_the_line_image_even_when_another_is_first() -> None:
    owner = vendor()
    product = product_for(owner, "Helmet", variable=True)
    red = VariantFactory.create(product=product, sku="HEL-RED")
    picture(product, "first", variant=red)
    chosen = picture(product, "second", variant=red)
    red.cover_image = chosen
    red.save()
    found = thumbnails([(product.pk, red.pk)])
    assert "second" in found[(product.pk, red.pk)].image.file.name


def test_a_language_with_a_dedicated_image_shows_only_it() -> None:
    owner = vendor()
    product = product_for(owner, "Box")
    picture(product, "neutral", is_cover=True)
    picture(product, "german", language="de", is_cover=True)
    picture(product, "french", language="fr", is_cover=True)
    key = (product.pk, None)
    assert "german" in thumbnails([key], "de")[key].image.file.name
    assert "neutral" in thumbnails([key], "en")[key].image.file.name
    assert "neutral" in thumbnails([key], "it")[key].image.file.name


def test_the_cart_page_follows_the_page_language(client: Client, settings: Any) -> None:
    german = other_language()
    cart = with_cart(client)
    product = product_for(vendor(), "Box")
    put_line(cart, product)
    picture(product, "neutral", is_cover=True)
    picture(product, "dedicated", language=german, is_cover=True)
    with translation.override(german):
        url = reverse("orders:cart")
    html = client.get(url).content.decode()
    assert len(imgs(html, "dedicated")) == 1
    assert not imgs(html, "neutral")  # a language with its own image shows only those


def test_a_product_with_images_only_in_another_language_shows_none() -> None:
    product = product_for(vendor(), "Box")
    picture(product, "german", language="de", is_cover=True)
    assert thumbnails([(product.pk, None)], "en") == {}


def test_the_alt_is_empty_when_it_only_repeats_the_line_name() -> None:
    assert present.thumbnail_alt("Red helmet", "") == ""
    assert present.thumbnail_alt("Red helmet", "Red helmet") == ""
    assert present.thumbnail_alt("Red helmet", "red helmet, side view") == ""
    assert present.thumbnail_alt("Red helmet", "Glossy shell with a white stripe") == (
        "Glossy shell with a white stripe"
    )


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_line_image_with_its_own_description_keeps_it_as_alt(
    client: Client, settings: Any, theme: str
) -> None:
    cart = with_cart(client)
    product = product_for(vendor(), "Helmet")
    put_line(cart, product)
    picture(product, "described", is_cover=True, alt="Glossy shell with a white stripe")
    html = cart_html(client, settings, theme)
    assert 'alt="Glossy shell with a white stripe"' in imgs(html, "described")[0]


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_a_line_image_that_repeats_the_name_is_decorative(
    client: Client, settings: Any, theme: str
) -> None:
    cart = with_cart(client)
    product = product_for(vendor(), "Helmet")
    put_line(cart, product)
    picture(product, "repeat", is_cover=True, alt="Helmet")
    html = cart_html(client, settings, theme)
    assert 'alt=""' in imgs(html, "repeat")[0]


def test_the_line_data_carries_the_thumbnail_and_no_price_changes(
    client: Client, settings: Any
) -> None:
    cart = with_cart(client)
    product = product_for(vendor(), "Helmet")
    put_line(cart, product)
    before = client.get(reverse("orders:cart")).content.decode()
    picture(product, "withpic", is_cover=True)
    after = client.get(reverse("orders:cart")).content.decode()
    strip = re.compile(r"<img[^>]*>|<div class=\"cart-line__(media|placeholder)\"[^>]*>(</div>)?")
    prices = re.compile(r"€\xa0[\d.,]+")
    assert prices.findall(before) == prices.findall(after)
    assert len(imgs(after, "withpic")) == 1
    assert strip.sub("", before).count("Helmet") == strip.sub("", after).count("Helmet")


def image_queries(captured: Any) -> int:
    return len([q for q in captured if "productimage" in q["sql"] or "rendition" in q["sql"]])


def test_the_cart_page_queries_do_not_grow_per_line(client: Client, settings: Any) -> None:
    cart = with_cart(client)
    owner = vendor()
    first = product_for(owner, "Lamp 0")
    picture(first, "pic0", is_cover=True)
    put_line(cart, first)
    client.get(reverse("orders:cart"))  # warm caches (content types, flags)
    with CaptureQueriesContext(connection) as one:
        client.get(reverse("orders:cart"))
    for index in range(1, 8):
        extra = product_for(owner, f"Lamp {index}")
        picture(extra, f"pic{index}", is_cover=True)
        picture(extra, f"alt{index}", language="de")
        put_line(cart, extra)
    with CaptureQueriesContext(connection) as eight:
        html = client.get(reverse("orders:cart")).content.decode()
    assert len([tag for tag in re.findall(r"<img[^>]*>", html) if "pic" in tag]) == 8
    # The images cost the same queries for 1 and for 8 lines: one prefetch for all of them.
    assert image_queries(eight.captured_queries) == image_queries(one.captured_queries) > 0
    # And the same cart without images costs exactly that much less (the lines' own texts and
    # stock queries are not the images').
    ProductImage.objects.all().delete()
    with CaptureQueriesContext(connection) as bare:
        client.get(reverse("orders:cart"))
    added = image_queries(eight.captured_queries) - image_queries(bare.captured_queries)
    assert len(eight) - len(bare) == added


def test_the_thumbnail_helper_runs_a_fixed_number_of_queries() -> None:
    owner = vendor()
    pairs: list[tuple[int, int | None]] = []
    for index in range(4):
        product = product_for(owner, f"Item {index}", variable=True)
        variant = VariantFactory.create(product=product, sku=f"IT-{index}")
        picture(product, f"p{index}", is_cover=True)
        picture(product, f"v{index}", variant=variant, is_cover=True)
        pairs += [(product.pk, None), (product.pk, variant.pk)]
    with CaptureQueriesContext(connection) as queries:
        found = thumbnails(pairs)
    assert len(found) == 8
    assert len(queries) <= 5


@pytest.mark.parametrize("theme", ["base", "ride"])
def test_the_checkout_review_lines_show_the_thumbnail_too(
    client: Client, settings: Any, theme: str
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    cart = with_cart(client)
    product = product_for(vendor(), "Blue vase")
    put_line(cart, product)
    picture(product, "reviewpic", is_cover=True)
    sign_off_details(client)
    html = client.get(reverse("orders:review")).content.decode()
    assert len(imgs(html, "reviewpic")) == 1
