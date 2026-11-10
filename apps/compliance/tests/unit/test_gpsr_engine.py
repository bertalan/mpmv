"""CMP.3: GPSR rules as pure functions (spec 11 CMP.3, regulation (EU) 2023/988 art. 19).

The product data block of article 19: manufacturer name, postal and electronic address; for a
manufacturer outside the EU, the EU responsible person; something that identifies the product.
Per-vendor defaults fill the blocks the product leaves empty, one whole block at a time.
"""

from apps.compliance import engine
from apps.compliance.engine import GpsrParties

MAKER = GpsrParties(
    manufacturer_name="Example Maker S.r.l.",
    manufacturer_address="Via Esempio 1, 00100 Roma",
    manufacturer_contact="safety@maker.example",
    manufacturer_country="IT",
)
OUTSIDE_EU = GpsrParties(
    manufacturer_name="Far Away Works Ltd",
    manufacturer_address="1 Example Road, Example City",
    manufacturer_contact="https://faraway.example/contact",
    manufacturer_country="CN",
)
EU_PERSON = GpsrParties(
    eu_responsible_name="EU Rep GmbH",
    eu_responsible_address="Beispielstrasse 1, 10115 Berlin",
    eu_responsible_contact="rep@eurep.example",
)


def facts(parties: GpsrParties, *identifiers: str) -> engine.GpsrFacts:
    return engine.GpsrFacts(parties=parties, identifiers=identifiers)


def test_complete_eu_manufacturer_with_an_identifier_misses_nothing() -> None:
    assert engine.gpsr_missing(facts(MAKER, "gtin")) == ()


def test_no_data_at_all_misses_every_manufacturer_field_and_the_identifier() -> None:
    assert engine.gpsr_missing(facts(GpsrParties())) == (
        "manufacturer_name",
        "manufacturer_address",
        "manufacturer_contact",
        "manufacturer_country",
        "product_identifier",
    )


def test_blank_values_count_as_missing() -> None:
    blank = GpsrParties(
        manufacturer_name="  ",
        manufacturer_address=MAKER.manufacturer_address,
        manufacturer_contact=MAKER.manufacturer_contact,
        manufacturer_country="IT",
    )
    assert engine.gpsr_missing(facts(blank, "batch")) == ("manufacturer_name",)


def test_a_product_needs_an_identifier() -> None:
    assert engine.gpsr_missing(facts(MAKER)) == ("product_identifier",)


def test_a_manufacturer_outside_the_eu_needs_the_eu_responsible_person() -> None:
    assert engine.gpsr_missing(facts(OUTSIDE_EU, "model_number")) == (
        "eu_responsible_name",
        "eu_responsible_address",
        "eu_responsible_contact",
    )


def test_with_the_eu_responsible_person_a_non_eu_manufacturer_is_complete() -> None:
    both = engine.merge_parties(OUTSIDE_EU, EU_PERSON)
    assert engine.gpsr_missing(facts(both, "gtin")) == ()


def test_an_eu_manufacturer_needs_no_eu_responsible_person() -> None:
    assert not engine.needs_eu_responsible("IT")
    assert not engine.needs_eu_responsible("EL")  # Greece, VAT spelling
    assert engine.needs_eu_responsible("CH")
    assert not engine.needs_eu_responsible("")  # unknown: reported as missing country instead


def test_the_product_block_wins_over_the_vendor_default_block() -> None:
    merged = engine.merge_parties(OUTSIDE_EU, MAKER)
    assert merged.manufacturer_name == "Far Away Works Ltd"
    assert merged.manufacturer_country == "CN"


def test_defaults_fill_a_whole_empty_block_never_single_fields() -> None:
    partial = GpsrParties(manufacturer_name="Only A Name Ltd")
    merged = engine.merge_parties(partial, MAKER)
    # The product started its own manufacturer block: the default does not complete it.
    assert merged.manufacturer_name == "Only A Name Ltd"
    assert merged.manufacturer_address == ""
    assert engine.gpsr_missing(facts(merged, "gtin")) == (
        "manufacturer_address",
        "manufacturer_contact",
        "manufacturer_country",
    )


def test_defaults_fill_the_eu_responsible_block_separately() -> None:
    vendor_defaults = engine.merge_parties(MAKER, EU_PERSON)
    merged = engine.merge_parties(OUTSIDE_EU, vendor_defaults)
    assert merged.manufacturer_name == "Far Away Works Ltd"
    assert merged.eu_responsible_name == "EU Rep GmbH"
    assert engine.gpsr_missing(facts(merged, "gtin")) == ()


def test_no_defaults_keep_the_product_block() -> None:
    assert engine.merge_parties(MAKER, None) == MAKER
    assert engine.merge_parties(None, MAKER) == MAKER
    assert engine.merge_parties(None, None) == GpsrParties()


def test_contact_is_an_email_or_a_web_address() -> None:
    assert engine.contact_error("safety@maker.example") is None
    assert engine.contact_error("https://maker.example/safety") is None
    assert engine.contact_error("") is None  # empty is "missing", not "invalid"
    assert engine.contact_error("call us") is not None
    assert engine.contact_error("ftp://maker.example") is not None
    assert engine.contact_error("a@b") is not None
