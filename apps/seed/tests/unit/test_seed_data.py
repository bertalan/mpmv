"""ARC.10: the demo data set is synthetic, complete and well formed (no database)."""

import re
from decimal import Decimal

from apps.core.countries import default_allowed_vendor_countries
from apps.seed import data
from apps.seed.images import placeholder_webp
from apps.seed.services import shipping_document
from apps.shipping import config_format
from apps.tax import engine as tax_engine

LETTERS = ("A", "B", "C", "D", "E")


def test_vendors_are_the_pseudonyms_a_to_e_with_reserved_email_domains() -> None:
    assert [vendor.letter for vendor in data.VENDORS] == list(LETTERS)
    for vendor in data.VENDORS:
        assert vendor.display_name.startswith(f"Vendor {vendor.letter} ")
        for email in (vendor.owner_email, vendor.contact_email):
            assert email.endswith("@example.org")
    assert data.OPERATOR_EMAIL.endswith("@example.org")


def test_vendors_cover_every_vat_regime_and_exemption_reason() -> None:
    assert {vendor.vat_regime for vendor in data.VENDORS} == set(tax_engine.REGIMES)
    reasons = {vendor.exempt_reason for vendor in data.VENDORS if vendor.exempt_reason}
    assert reasons == set(tax_engine.EXEMPT_REASONS)
    # Ordinary, forfettario, a non-Italian EU company and the cross-border franchise.
    assert {"RF01", "RF19", "RF18", "RF20"} <= {vendor.fiscal_regime for vendor in data.VENDORS}


def test_vendors_are_established_in_the_default_allowed_countries() -> None:
    allowed = set(default_allowed_vendor_countries())
    assert {vendor.country for vendor in data.VENDORS} <= allowed
    assert len({vendor.country for vendor in data.VENDORS}) > 1


def test_products_have_en_and_it_titles_and_positive_decimal_prices() -> None:
    products = [item for letter in LETTERS for item in data.PRODUCTS[letter]]
    assert 40 <= len(products) <= 60
    titles = [item.title["en"] for item in products]
    assert len(set(titles)) == len(titles)
    for item in products:
        assert set(item.title) == {"en", "it"}
        assert isinstance(item.price, Decimal)
        assert item.price > 0
        assert item.price == item.price.quantize(Decimal("0.01"))
        if item.sale_price is not None:
            assert 0 < item.sale_price < item.price
        assert item.weight > 0
    assert any(item.axes for item in products)  # variable products with variants


def test_catalog_structure_is_translated_in_every_planned_language() -> None:
    for node in [*data.CATEGORIES, *(c for n in data.CATEGORIES for c in n.children)]:
        assert set(node.names) == {"en", "it", "de", "fr", "es"}
        assert set(node.slugs) == {"en", "it", "de", "fr", "es"}
        for slug in node.slugs.values():
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug)
    for node in data.TERMS:
        assert set(node.names) == {"en", "it", "de", "fr", "es"}


def test_every_vendor_shipping_file_is_well_formed_and_treats_special_areas() -> None:
    for vendor in data.VENDORS:
        document = shipping_document(vendor)
        parsed = config_format.parse_json(document)
        assert parsed.version == config_format.VERSION
        treated = {t["area"] for m in document["methods"] for t in m["area_treatments"]}
        assert treated == {*vendor.island_areas, *vendor.quote_areas, *vendor.closed_areas}
    islands = {area for vendor in data.VENDORS for area in vendor.island_areas}
    assert {"it-sicily", "it-sardinia"} <= islands


def test_legal_pages_are_marked_as_demo_placeholders() -> None:
    for page in data.LEGAL_PAGES.values():
        assert "(demo)" in page.title["en"]
        assert "DEMO PLACEHOLDER" in page.body["en"]
        assert "SEGNAPOSTO" in page.body["it"]


def test_an_other_exemption_names_the_vat_country_of_its_law() -> None:
    """TAX.13: the "other" reason stores the country of its law: the VAT number's country."""
    for vendor in data.VENDORS:
        if vendor.exempt_reason == "other":
            assert vendor.exempt_country == vendor.country
        else:
            assert vendor.exempt_country == ""


def test_seed_top_level_categories_and_pages_have_seed_slugs() -> None:
    for node in data.CATEGORIES:
        assert all(slug.startswith(data.SEED_SLUG_PREFIX) for slug in node.slugs.values())
    assert all(slug.startswith("demo-") for slug in data.HOW_IT_WORKS.slug.values())


def test_placeholder_images_are_reproducible_webp() -> None:
    first = placeholder_webp("home-hero", (196, 112, 74))
    assert first[:4] == b"RIFF"
    assert first[8:12] == b"WEBP"
    assert placeholder_webp("home-hero", (196, 112, 74)) == first
