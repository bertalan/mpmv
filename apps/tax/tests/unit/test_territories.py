"""TAX.7: which special territory an address lies in (spec 05, case T15). No ORM."""

import pytest

from apps.tax import engine

RULES = [
    engine.territory_rule("IT-LIVIGNO", "IT", engine.OUTSIDE_VAT_AREA, ["23041"]),
    engine.territory_rule("ES-CN", "ES", engine.OUTSIDE_VAT_AREA, ["35*", "38*"]),
    engine.territory_rule("FI-01", "FI", engine.OUTSIDE_VAT_AREA, ["22*"]),
    engine.territory_rule("XI", "GB", engine.SPECIAL, ["BT*"]),
    engine.territory_rule("FR-971", "FR", engine.OUTSIDE_VAT_AREA, ["971*"]),
    engine.territory_rule("FR-974", "FR", engine.OUTSIDE_VAT_AREA, ["974*"]),
    engine.territory_rule("FR-MF", "FR", engine.OUTSIDE_VAT_AREA, ["97150"]),
    engine.territory_rule("FR-BL", "FR", engine.OUTSIDE_VAT_AREA, ["97133"]),
    engine.territory_rule("PT-20", "PT", engine.OWN_RATES, ["9000..9499"]),
]


def test_the_reason_of_an_outside_territory_is_the_one_of_spec_05() -> None:
    assert engine.OUTSIDE_VAT_TERRITORY == "outside_vat_territory"


# T15: Canary Islands / Livigno / Aland -> 0% outside_vat_territory
@pytest.mark.parametrize(
    ("country", "postcode", "code"),
    [
        ("IT", "23041", "IT-LIVIGNO"),
        ("ES", "35001", "ES-CN"),
        ("ES", "38 650", "ES-CN"),
        ("FI", "22100", "FI-01"),
        ("AX", "22100", "FI-01"),  # Aland has an ISO code of its own
    ],
)
def test_t15_an_address_in_an_outside_territory_is_found(
    country: str, postcode: str, code: str
) -> None:
    rule = engine.find_territory(country, postcode, RULES)
    assert rule is not None
    assert (rule.code, rule.vat_status) == (code, engine.OUTSIDE_VAT_AREA)
    assert engine.is_outside_vat_territory(country, postcode, RULES) is True


@pytest.mark.parametrize(
    ("country", "postcode"),
    [
        ("IT", "35100"),  # Padova is not the Canary Islands (ES 35*)
        ("IT", "23100"),  # Sondrio, not Livigno
        ("ES", "28001"),
        ("FI", "00100"),
        ("DE", "22041"),  # right digits, wrong country
        ("IT", ""),
        ("ES", "  "),
    ],
)
def test_an_address_outside_every_territory_is_not_found(country: str, postcode: str) -> None:
    assert engine.find_territory(country, postcode, RULES) is None
    assert engine.is_outside_vat_territory(country, postcode, RULES) is False


def test_only_the_outside_status_is_outside_the_vat_territory() -> None:
    assert engine.find_territory("GB", "BT1 1AA", RULES) is not None  # XI: special, D-02
    assert engine.is_outside_vat_territory("GB", "BT1 1AA", RULES) is False
    assert engine.find_territory("PT", "9000-001", RULES) is not None  # Madeira: own rates
    assert engine.is_outside_vat_territory("PT", "9000-001", RULES) is False


@pytest.mark.parametrize(
    ("alias", "home"),
    [
        ("AX", "FI"),
        ("GP", "FR"),
        ("MQ", "FR"),
        ("GF", "FR"),
        ("RE", "FR"),
        ("YT", "FR"),
        ("MF", "FR"),
        ("BL", "FR"),
    ],
)
def test_a_territory_with_an_iso_code_of_its_own_is_part_of_its_country(
    alias: str, home: str
) -> None:
    assert engine.territory_country(alias) == home
    assert engine.territory_country(home) == home


def test_greece_is_el_or_gr() -> None:
    rules = [engine.territory_rule("GR-69", "GR", engine.OUTSIDE_VAT_AREA, ["63086"])]
    assert engine.is_outside_vat_territory("EL", "630 86", rules) is True


def test_an_invalid_pattern_fails_loudly() -> None:
    with pytest.raises(engine.TerritoryConfigError, match="ES-XX"):
        engine.territory_rule("ES-XX", "ES", engine.OUTSIDE_VAT_AREA, ["3*5"])


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="status"):
        engine.territory_rule("ES-XX", "ES", "elsewhere", ["35*"])


# An ISO code of its own names the territory: no postcode needed, none can send it to mainland VAT.
@pytest.mark.parametrize(
    ("country", "postcode", "code"),
    [
        ("GP", "", "FR-971"),
        ("GP", "00000", "FR-971"),
        ("RE", "75001", "FR-974"),
        ("AX", "00100", "FI-01"),
        ("AX", "", "FI-01"),
        ("ax", " ", "FI-01"),
        ("MF", "", "FR-MF"),
        ("MF", "75001", "FR-MF"),
        ("BL", "", "FR-BL"),
        ("bl", "00000", "FR-BL"),
    ],
)
def test_an_alias_country_fails_closed_whatever_the_postcode(
    country: str, postcode: str, code: str
) -> None:
    rule = engine.find_territory(country, postcode, RULES)
    assert rule is not None
    assert rule.code == code
    assert engine.is_outside_vat_territory(country, postcode, RULES) is True


def test_an_alias_whose_territory_is_not_configured_is_not_found() -> None:
    assert engine.find_territory("MQ", "", RULES) is None


def test_every_alias_names_a_territory_code() -> None:
    assert engine.TERRITORY_BY_ALIAS_COUNTRY == {
        "AX": "FI-01",
        "GP": "FR-971",
        "MQ": "FR-972",
        "GF": "FR-973",
        "RE": "FR-974",
        "YT": "FR-976",
        "MF": "FR-MF",
        "BL": "FR-BL",
    }


@pytest.mark.parametrize(
    ("country", "postcode"),
    [
        ("ES", "3500"),
        ("ES", "350012"),
        ("FI", "22"),
        ("FR", "9711"),
        ("FR", "971100"),
        ("ES", "35"),
    ],
)
def test_a_prefix_never_matches_a_postcode_of_the_wrong_length(country: str, postcode: str) -> None:
    assert engine.find_territory(country, postcode, RULES) is None


@pytest.mark.parametrize("postcode", ["97150", "97133"])
def test_saint_martin_and_saint_barthelemy_are_outside_as_france_with_their_postcode(
    postcode: str,
) -> None:
    # Both also fall under the 971* prefix of Guadeloupe: whichever rule matches first, the
    # status is the same.
    assert engine.find_territory("FR", postcode, RULES) is not None
    assert engine.is_outside_vat_territory("FR", postcode, RULES) is True
    only = [r for r in RULES if r.code in {"FR-MF", "FR-BL"}]
    assert engine.is_outside_vat_territory("FR", postcode, only) is True
