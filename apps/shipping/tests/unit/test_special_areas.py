"""SHP.14: special areas (islands, remote and extra-VAT territories) and the treatment a shipping
method gives them, as pure functions (spec 06 "Standard template and special areas").

The engine tells SHP.3 which special area an address is in and which treatment applies; the
quote itself (`quote_shipping`) is SHP.3. Without a treatment of its own, a method gives an area
the default: an indicative cost subject to quote.
"""

from decimal import Decimal

import pytest

from apps.shipping import engine
from apps.shipping.engine import AreaTreatment, Destination, SpecialAreaSnap

SARDINIA = SpecialAreaSnap(
    code="it-sardinia",
    kind=engine.ISLAND,
    countries=frozenset({"IT"}),
    postcode_patterns=("07*", "08*", "09*"),
    states=("SS", "NU", "OR", "CA", "SU"),
)
LIVIGNO = SpecialAreaSnap(
    code="it-livigno",
    kind=engine.EXTRA_VAT,
    countries=frozenset({"IT"}),
    postcode_patterns=("23041",),
    territory_code="IT-LIVIGNO",
)
ALAND = SpecialAreaSnap(
    code="fi-aland",
    kind=engine.EXTRA_VAT,
    countries=frozenset({"FI", "AX"}),
    postcode_patterns=("22*", "AX-22*"),
    territory_code="FI-01",
)
AREAS = (SARDINIA, LIVIGNO, ALAND)
FIXED_15 = AreaTreatment(mode=engine.SURCHARGE, surcharge_type=engine.FIXED, surcharge=Decimal(15))


# --- Which special area an address is in


@pytest.mark.parametrize(
    ("dest", "area"),
    [
        (Destination("IT", "07100"), "it-sardinia"),  # Sassari, by postcode
        (Destination("IT", "09124"), "it-sardinia"),  # Cagliari
        (Destination("IT", "", "ss"), "it-sardinia"),  # by province, no postcode
        (Destination("IT", "23041"), "it-livigno"),
        (Destination("FI", "22100"), "fi-aland"),
        (Destination("AX", "AX-22100"), "fi-aland"),  # the territory's own ISO code
        (Destination("IT", "20121"), None),  # Milan: mainland
        (Destination("IT", "29121"), None),  # regression case 1: never an island
        (Destination("FR", "07100"), None),  # the postcode of another country
        (Destination("IT", ""), None),  # no postcode, no province
    ],
)
def test_an_address_is_in_at_most_one_special_area(dest: Destination, area: str | None) -> None:
    found = engine.resolve_special_area(AREAS, dest)
    assert (found.code if found else None) == area


def test_a_postcode_match_wins_over_a_province_match() -> None:
    by_province = SpecialAreaSnap(
        code="a-province", kind=engine.REMOTE, countries=frozenset({"IT"}), states=("SO",)
    )
    found = engine.resolve_special_area((by_province, LIVIGNO), Destination("IT", "23041", "SO"))
    assert found == LIVIGNO


def test_an_extra_vat_area_wins_over_an_island_on_the_same_postcode() -> None:
    carrier_list = SpecialAreaSnap(
        code="carrier-remote",
        kind=engine.REMOTE,
        countries=frozenset({"IT"}),
        postcode_patterns=("23041",),
    )
    found = engine.resolve_special_area((carrier_list, LIVIGNO), Destination("IT", "23041"))
    assert found == LIVIGNO


def test_the_result_does_not_depend_on_the_order_of_the_areas() -> None:
    a = SpecialAreaSnap(
        code="b-list", kind=engine.REMOTE, countries=frozenset({"GR"}), postcode_patterns=("846*",)
    )
    b = SpecialAreaSnap(
        code="a-list", kind=engine.REMOTE, countries=frozenset({"GR"}), postcode_patterns=("84600",)
    )
    dest = Destination("GR", "846 00")
    assert (
        engine.resolve_special_area((a, b), dest) == engine.resolve_special_area((b, a), dest) == b
    )


def test_a_malformed_area_pattern_is_refused_when_the_snapshot_is_built() -> None:
    with pytest.raises(ValueError, match=r"\.\."):
        SpecialAreaSnap(
            code="bad",
            kind=engine.ISLAND,
            countries=frozenset({"IT"}),
            postcode_patterns=("07000-07999",),
        )


# --- Which treatment applies (Done when: Sardinia indicative; a fixed surcharge override)


def test_without_a_treatment_of_its_own_a_method_gives_an_indicative_cost_subject_to_quote() -> (
    None
):
    decision = engine.special_area_treatment(AREAS, {}, Destination("IT", "07100"))

    assert decision is not None
    assert decision.area == SARDINIA
    assert decision.is_default
    assert decision.treatment == engine.DEFAULT_AREA_TREATMENT
    assert decision.treatment.mode == engine.INDICATIVE
    assert decision.is_indicative
    assert decision.requests_quote  # the buyer requests a quote for the exact price
    assert decision.offers_rate
    # the estimate the buyer sees: the method's own cost
    assert decision.cost(Decimal("9.90"), Decimal("2.000")) == Decimal("9.90")


def test_a_vendor_override_to_a_fixed_surcharge_applies() -> None:
    decision = engine.special_area_treatment(
        AREAS, {"it-sardinia": FIXED_15}, Destination("IT", "07100")
    )

    assert decision is not None
    assert not decision.is_default
    assert not decision.is_indicative
    assert not decision.requests_quote
    assert decision.offers_rate
    assert decision.cost(Decimal("9.90"), Decimal("2.000")) == Decimal("24.90")


def test_an_address_outside_every_special_area_has_no_decision() -> None:
    assert (
        engine.special_area_treatment(AREAS, {"it-sardinia": FIXED_15}, Destination("IT", "20121"))
        is None
    )


def test_a_treatment_for_another_area_does_not_apply() -> None:
    decision = engine.special_area_treatment(
        AREAS, {"it-livigno": FIXED_15}, Destination("IT", "07100")
    )
    assert decision is not None
    assert decision.is_default


@pytest.mark.parametrize(
    ("treatment", "cost"),
    [
        (AreaTreatment(engine.SURCHARGE, engine.FIXED, Decimal("15.00")), Decimal("25.00")),
        (AreaTreatment(engine.SURCHARGE, engine.PERCENT, Decimal("12.50")), Decimal("11.25")),
        # 2.345 kg x 1.10 = 2.5795 -> 2.58 (ROUND_HALF_UP, once, at the end)
        (AreaTreatment(engine.SURCHARGE, engine.PER_KG, Decimal("1.10")), Decimal("12.58")),
        # an estimate may carry a surcharge too
        (AreaTreatment(engine.INDICATIVE, engine.FIXED, Decimal("5.00")), Decimal("15.00")),
        (AreaTreatment(engine.INDICATIVE), Decimal("10.00")),
    ],
)
def test_surcharges_are_fixed_percent_or_per_kg(treatment: AreaTreatment, cost: Decimal) -> None:
    assert engine.surcharged_cost(Decimal("10.00"), Decimal("2.345"), treatment) == cost


def test_a_percent_surcharge_rounds_half_up_to_cents() -> None:
    treatment = AreaTreatment(engine.SURCHARGE, engine.PERCENT, Decimal("10"))
    # 0.05 x 10% = 0.005 -> 0.01
    assert engine.surcharged_cost(Decimal("0.05"), Decimal("1"), treatment) == Decimal("0.06")


@pytest.mark.parametrize(
    ("mode", "offers_rate", "requests_quote"),
    [
        (engine.INDICATIVE, True, True),
        (engine.SURCHARGE, True, False),
        (engine.QUOTE_ONLY, False, True),
        (engine.UNAVAILABLE, False, False),
        (engine.BLOCKED, False, False),
    ],
)
def test_what_each_mode_means_for_the_quote(
    mode: str, offers_rate: bool, requests_quote: bool
) -> None:
    surcharge_type = engine.FIXED if mode == engine.SURCHARGE else ""
    treatment = AreaTreatment(mode, surcharge_type, Decimal(1) if surcharge_type else Decimal(0))
    decision = engine.special_area_treatment(
        AREAS, {"it-sardinia": treatment}, Destination("IT", "07100")
    )

    assert decision is not None
    assert decision.offers_rate is offers_rate
    assert decision.requests_quote is requests_quote
    assert decision.is_blocked is (mode == engine.BLOCKED)
    assert decision.is_unavailable is (mode == engine.UNAVAILABLE)
    if not offers_rate:
        assert decision.cost(Decimal("9.90"), Decimal("1.000")) is None


def test_extra_delivery_days_lengthen_the_indicative_delivery_time() -> None:
    treatment = AreaTreatment(engine.SURCHARGE, engine.FIXED, Decimal(5), extra_days=3)
    decision = engine.special_area_treatment(
        AREAS, {"it-sardinia": treatment}, Destination("IT", "07100")
    )

    assert decision is not None
    assert decision.delivery_days(2, 5) == (5, 8)
    assert decision.delivery_days(None, 5) == (None, 8)
    assert decision.delivery_days(None, None) == (None, None)


def test_an_extra_vat_area_names_its_tax_territory() -> None:
    decision = engine.special_area_treatment(AREAS, {}, Destination("IT", "23041"))
    assert decision is not None
    assert decision.area.territory_code == "IT-LIVIGNO"


# --- The rules a treatment must follow (the model and the database enforce the same)


@pytest.mark.parametrize(
    ("mode", "surcharge_type", "surcharge", "extra_days", "fields"),
    [
        (engine.INDICATIVE, "", Decimal(0), 0, set()),
        (engine.SURCHARGE, engine.FIXED, Decimal("15.00"), 2, set()),
        (engine.SURCHARGE, "", Decimal(0), 0, {"surcharge_type"}),  # a surcharge of what?
        (engine.QUOTE_ONLY, engine.FIXED, Decimal(5), 0, {"surcharge_type"}),
        (engine.BLOCKED, "", Decimal(0), 2, {"extra_days"}),
        (engine.UNAVAILABLE, "", Decimal(0), 1, {"extra_days"}),
        (engine.INDICATIVE, "", Decimal(3), 0, {"surcharge"}),  # an amount without a type
        (engine.SURCHARGE, engine.FIXED, Decimal(-1), 0, {"surcharge"}),
        (engine.SURCHARGE, engine.PERCENT, Decimal(0), 0, set()),
        ("cheap", "", Decimal(0), 0, {"mode"}),
        (engine.SURCHARGE, "per_parcel", Decimal(1), 0, {"surcharge_type"}),
    ],
)
def test_treatment_rules(
    mode: str, surcharge_type: str, surcharge: Decimal, extra_days: int, fields: set[str]
) -> None:
    assert set(engine.treatment_errors(mode, surcharge_type, surcharge, extra_days)) == fields


@pytest.mark.parametrize(
    ("raw", "code"), [("it-livigno", "IT-LIVIGNO"), (" es-cn ", "ES-CN"), ("FI-01", "FI-01")]
)
def test_territory_codes_are_normalized(raw: str, code: str) -> None:
    assert engine.normalize_territory_code(raw) == code


@pytest.mark.parametrize("raw", ["", "E", "ES_CN", "ES-", "ES-CANARY-ISLANDS", "1T-X"])
def test_malformed_territory_codes_are_refused(raw: str) -> None:
    with pytest.raises(engine.InvalidTerritoryCode):
        engine.normalize_territory_code(raw)
