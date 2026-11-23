"""THM.23 3/3: every seeded priced product has a computable consumer price (TAX.12, ORD.19).

Done when: "the seed creates a tax profile for every seeded vendor and an operator tax setup, so
every seeded priced product has a computable consumer price (otherwise ORD.19 treats the product
as unpriced and the page shows no buy form); a test asserts that every seeded product with a net
price has a consumer price".

The price goes through `apps.tax.prices.consumer_price`, the public service behind the product
page, cards and cart. Every destination a visitor gets before giving an address is checked: the
default country of each configured language and the operator's country (`current_operator()`).
The module runs under the default languages and under `MPMV_LANGUAGES=it,de` alike.
"""

from decimal import Decimal

import pytest
from django.utils import translation

from apps.catalog.models import Product, Variant
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.core.services import current_operator
from apps.seed.services import TEST_CONTEXT, seed, seed_vendors
from apps.tax import engine as tax_engine
from apps.tax.models import VendorTaxProfileTranslation
from apps.tax.prices import PriceUnavailable, consumer_price
from apps.tax.services import has_tax_profile

pytestmark = pytest.mark.django_db


def _destinations() -> set[str]:
    operator_country = current_operator().country
    return {
        tax_engine.estimate_price_country(
            delivery_country="", language=language, operator_country=operator_country
        )[0]
        for language in configured_languages()
    } | {operator_country}


def _net_prices() -> list[tuple[Product, Decimal, str]]:
    """(product, net price, what it is) for every net price a seeded product shows."""
    vendors = seed_vendors()
    prices: list[tuple[Product, Decimal, str]] = []
    for product in Product.objects.filter(vendor__in=vendors).select_related("vendor"):
        for label, net in (("price", product.price_net), ("sale", product.sale_price_net)):
            if net is not None:
                prices.append((product, net, label))
    for variant in Variant.objects.filter(product__vendor__in=vendors).select_related(
        "product__vendor"
    ):
        if variant.price_net_override is not None:
            prices.append((variant.product, variant.price_net_override, f"variant {variant.pk}"))
    return prices


def test_every_seeded_vendor_has_a_tax_profile(demo_seed: ChangeSet) -> None:
    vendors = seed_vendors()
    assert vendors
    assert [v.slug for v in vendors if not has_tax_profile(v)] == []


def test_every_seeded_net_price_has_a_consumer_price(demo_seed: ChangeSet) -> None:
    prices = _net_prices()
    assert prices, "the seed has priced products"
    missing: list[str] = []
    for country in sorted(_destinations()):
        for product, net, label in prices:
            try:
                price = consumer_price(
                    net, product.fiscal_category, vendor=product.vendor, country=country
                )
            except PriceUnavailable as exc:
                missing.append(f"{country} {product.sku or product.pk} {label}: {exc}")
                continue
            assert price.gross >= net
    assert not missing, "\n".join(missing)


def test_the_estimated_price_of_every_language_is_computable(demo_seed: ChangeSet) -> None:
    """Without a forced country, as the product page asks before the buyer gives an address."""
    prices = _net_prices()
    for language in configured_languages():
        with translation.override(language):
            for product, net, _label in prices:
                consumer_price(net, product.fiscal_category, vendor=product.vendor)


def test_a_second_run_still_reports_no_change(demo_seed: ChangeSet) -> None:
    assert seed(ctx=TEST_CONTEXT).is_empty


def test_a_seeded_profile_without_its_legal_note_gets_it_back(demo_seed: ChangeSet) -> None:
    """A database seeded before the note (STAGE) gets it on the next run, and only then."""
    notes = VendorTaxProfileTranslation.objects.filter(profile__vendor__in=seed_vendors())
    assert notes.exists()
    notes.delete()
    again = seed(ctx=TEST_CONTEXT)
    assert not again.is_empty
    assert notes.exists()
    assert seed(ctx=TEST_CONTEXT).is_empty
