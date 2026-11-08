"""SHP.14: special areas as data, the seeded lists, the treatment each shipping method gives
them, and the audited writes (spec 06 "Standard template and special areas").

Done when: a Sardinia address shows an indicative cost with a request-quote action; a vendor
override to a fixed surcharge applies. The quote itself (`quote_shipping`) is SHP.3: these tests
check the decision the shipping services hand to it.
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping import engine
from apps.shipping.engine import Destination
from apps.shipping.models import ShippingMethod, SpecialArea, SpecialAreaTreatment, WeightBand
from apps.shipping.services import (
    area_treatments,
    delete_config,
    save_config,
    special_area_decision,
    special_area_snaps,
)
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingZoneFactory,
    SpecialAreaFactory,
    SpecialAreaTreatmentFactory,
    WeightBandFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:shipping", source="admin", ip="127.0.0.1")

SEEDED_ISLANDS_AND_REMOTE = {
    "it-sardinia",
    "it-sicily",
    "it-minor-islands",
    "it-venice-lagoon",
    "es-balearic-islands",
    "fr-corsica",
    "gr-crete",
    "gr-aegean-islands",
    "pt-madeira",
    "pt-azores",
    "gb-highlands-islands",
}
SEEDED_EXTRA_VAT = {
    "it-livigno": "IT-LIVIGNO",
    "it-campione": "IT-CAMPIONE",
    "es-canary-islands": "ES-CN",
    "es-ceuta": "ES-CE",
    "es-melilla": "ES-ML",
    "fr-guadeloupe": "FR-971",
    "fr-martinique": "FR-972",
    "fr-guiana": "FR-973",
    "fr-reunion": "FR-974",
    "fr-mayotte": "FR-976",
    "fi-aland": "FI-01",
    "gr-mount-athos": "GR-69",
    "de-heligoland": "DE-HELGOLAND",
    "de-busingen": "DE-BUSINGEN",
}


# --- The seeded lists


def test_the_special_areas_of_the_standard_template_are_seeded() -> None:
    stored = dict(SpecialArea.objects.values_list("code", "territory_code"))
    assert set(stored) >= SEEDED_ISLANDS_AND_REMOTE
    for code, territory in SEEDED_EXTRA_VAT.items():
        assert stored.get(code) == territory, code
    for area in SpecialArea.objects.all():
        area.full_clean()  # every seeded list passes the shared validators


@pytest.mark.parametrize(
    ("dest", "area"),
    [
        (Destination("IT", "07100"), "it-sardinia"),
        (Destination("IT", "09124"), "it-sardinia"),
        (Destination("IT", "", "NU"), "it-sardinia"),
        (Destination("IT", "90133"), "it-sicily"),
        (Destination("IT", "80073"), "it-minor-islands"),  # Capri
        (Destination("IT", "30124"), "it-venice-lagoon"),
        (Destination("ES", "07001"), "es-balearic-islands"),
        (Destination("FR", "20000"), "fr-corsica"),
        (Destination("GR", "70100"), "gr-crete"),  # regression case 2
        (Destination("GR", "846 00"), "gr-aegean-islands"),  # regression cases 2 and 5
        (Destination("PT", "9000-001"), "pt-madeira"),  # regression case 4
        (Destination("PT", "9400-001"), "pt-madeira"),
        (Destination("PT", "9500-001"), "pt-azores"),
        (Destination("GB", "HS1 2AA"), "gb-highlands-islands"),
        (Destination("IT", "23041"), "it-livigno"),
        (Destination("IT", "22061"), "it-campione"),
        (Destination("ES", "35001"), "es-canary-islands"),
        (Destination("ES", "38001"), "es-canary-islands"),
        (Destination("ES", "51001"), "es-ceuta"),
        (Destination("ES", "52001"), "es-melilla"),
        (Destination("FR", "97110"), "fr-guadeloupe"),
        (Destination("GP", "97110"), "fr-guadeloupe"),
        (Destination("FR", "97400"), "fr-reunion"),
        (Destination("FI", "22100"), "fi-aland"),
        (Destination("AX", "AX-22100"), "fi-aland"),
        (Destination("GR", "63086"), "gr-mount-athos"),
        (Destination("DE", "27498"), "de-heligoland"),
        (Destination("DE", "78266"), "de-busingen"),
    ],
)
def test_seeded_areas_match_their_addresses(dest: Destination, area: str) -> None:
    found = engine.resolve_special_area(special_area_snaps(), dest)
    assert found is not None
    assert found.code == area


@pytest.mark.parametrize(
    "dest",
    [
        # regression case 1: mainland Italy, never an island
        *(
            Destination("IT", code)
            for code in ("70123", "28100", "81100", "82100", "83100", "29121", "31100", "37010")
        ),
        Destination("GR", "10100"),  # Athens (case 2)
        Destination("PT", "1000-001"),  # Lisbon (case 4)
        Destination("ES", "28001"),  # Madrid
        Destination("FR", "75001"),  # Paris
        Destination("DE", "10115"),  # Berlin
        Destination("GB", "SW1A 1AA"),
        Destination("IT", ""),
    ],
)
def test_mainland_addresses_are_in_no_seeded_area(dest: Destination) -> None:
    assert engine.resolve_special_area(special_area_snaps(), dest) is None


# --- The model rules


def test_a_special_area_is_normalized_on_validation() -> None:
    area = SpecialAreaFactory.build(
        code="Carrier-Remote",
        countries=["el"],
        postcode_patterns=[" 846* "],
        states=["ss"],
        kind=engine.EXTRA_VAT,
        territory_code="gr-x1",
    )
    area.full_clean()
    assert (area.code, area.countries, area.postcode_patterns, area.states) == (
        "carrier-remote",
        ["GR"],
        ["846*"],
        ["SS"],
    )
    assert area.territory_code == "GR-X1"


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"postcode_patterns": [], "states": []}, "__all__"),  # an area needs a list
        ({"postcode_patterns": ["07000-07999"]}, "postcode_patterns"),  # the old range form
        ({"countries": ["*"]}, "__all__"),
        ({"countries": []}, "countries"),
        ({"kind": engine.EXTRA_VAT, "territory_code": ""}, "__all__"),
        ({"kind": engine.ISLAND, "territory_code": "IT-X"}, "__all__"),
        ({"territory_code": "IT_X", "kind": engine.EXTRA_VAT}, "territory_code"),
        ({"kind": "volcano"}, "kind"),
    ],
)
def test_special_area_rules(fields: dict[str, object], error: str) -> None:
    area = SpecialAreaFactory.build(**fields)
    with pytest.raises(ValidationError) as raised:
        area.full_clean()
    assert error in raised.value.message_dict


def test_the_database_refuses_an_extra_vat_area_without_its_territory() -> None:
    with pytest.raises(IntegrityError):
        SpecialAreaFactory.create(kind=engine.EXTRA_VAT, territory_code="")


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"mode": engine.SURCHARGE, "surcharge_type": ""}, "surcharge_type"),
        ({"mode": engine.BLOCKED, "extra_days": 2}, "extra_days"),
        (
            {"mode": engine.QUOTE_ONLY, "surcharge_type": engine.FIXED, "surcharge": 5},
            "surcharge_type",
        ),
        ({"surcharge": Decimal("-1.00"), "surcharge_type": engine.FIXED}, "surcharge"),
    ],
)
def test_treatment_rules_are_model_validation(fields: dict[str, object], error: str) -> None:
    treatment = SpecialAreaTreatmentFactory.build(
        method=ShippingMethodFactory.create(), area=SpecialAreaFactory.create(), **fields
    )
    with pytest.raises(ValidationError) as raised:
        treatment.full_clean()
    assert error in raised.value.message_dict


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": engine.SURCHARGE, "surcharge_type": ""},
        {"mode": engine.BLOCKED, "extra_days": 2},
        {"mode": engine.UNAVAILABLE, "surcharge_type": engine.FIXED, "surcharge": 5},
        {"surcharge": Decimal(3)},  # an amount without a type
        {"mode": "cheap"},
    ],
)
def test_the_database_refuses_an_inconsistent_treatment(fields: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        SpecialAreaTreatmentFactory.create(**fields)


def test_a_method_treats_an_area_once() -> None:
    first = SpecialAreaTreatmentFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        SpecialAreaTreatmentFactory.create(method=first.method, area=first.area)
    # another method of the same vendor may treat it differently
    other = ShippingMethodFactory.create(vendor=first.method.vendor, zone=first.method.zone)
    SpecialAreaTreatmentFactory.create(method=other, area=first.area, fixed_surcharge=True)


def test_treatments_are_scoped_to_their_vendor() -> None:
    mine = SpecialAreaTreatmentFactory.create()
    SpecialAreaTreatmentFactory.create()
    assert list(SpecialAreaTreatment.objects.for_vendor(mine.method.vendor)) == [mine]


# --- Done when: Sardinia, indicative by default; a vendor's fixed surcharge applies


def sardinia_method() -> tuple[ShippingMethod, Destination]:
    method = ShippingMethodFactory.create(default=True)
    WeightBandFactory.create(method=method, from_kg=Decimal(0), to_kg=Decimal(5))  # 9.90
    return method, Destination("IT", "07100")


def test_a_sardinia_address_gets_an_indicative_cost_with_a_request_quote_action() -> None:
    method, sassari = sardinia_method()

    decision = special_area_decision(method, sassari)

    assert decision is not None
    assert decision.area.code == "it-sardinia"
    assert decision.is_default
    assert decision.is_indicative
    assert decision.requests_quote  # SHP.3 returns IndicativeRates: the buyer requests a quote
    band = WeightBand.objects.get(method=method)
    assert decision.cost(band.cost_net, Decimal("2.000")) == Decimal("9.90")  # the estimate


def test_a_vendor_override_to_a_fixed_surcharge_applies() -> None:
    method, sassari = sardinia_method()
    treatment = SpecialAreaTreatment(
        method=method,
        area=SpecialArea.objects.get(code="it-sardinia"),
        mode=engine.SURCHARGE,
        surcharge_type=engine.FIXED,
        surcharge=Decimal("15.00"),
        extra_days=2,
    )
    save_config(treatment, ctx=CTX)

    decision = special_area_decision(method, sassari)

    assert decision is not None
    assert not decision.is_default
    assert not decision.requests_quote
    assert decision.cost(Decimal("9.90"), Decimal("2.000")) == Decimal("24.90")
    assert decision.delivery_days(2, 5) == (4, 7)
    entry = AuditLog.objects.get(action="shipping.specialareatreatment.created")
    assert entry.vendor_key == str(method.vendor.public_id)
    assert entry.after is not None
    assert entry.after["surcharge_type"] == engine.FIXED


def test_another_vendor_at_the_same_address_keeps_the_default() -> None:
    method, sassari = sardinia_method()
    SpecialAreaTreatmentFactory.create(
        method=method, area=SpecialArea.objects.get(code="it-sardinia"), fixed_surcharge=True
    )
    other, _ = sardinia_method()

    decision = special_area_decision(other, sassari)

    assert decision is not None
    assert decision.is_default


def test_area_treatments_are_keyed_by_area_code() -> None:
    treatment = SpecialAreaTreatmentFactory.create(fixed_surcharge=True)
    assert area_treatments(treatment.method) == {
        treatment.area.code: engine.AreaTreatment(
            engine.SURCHARGE, engine.FIXED, Decimal("15.00"), 0
        )
    }


# --- Audited writes


def test_a_special_area_is_written_through_the_audited_service() -> None:
    area = SpecialAreaFactory.build(code="carrier-remote", kind=engine.REMOTE)
    preview = save_config(area, ctx=CTX, dry_run=True)
    assert preview.summary() == {"create": 1, "update": 0, "delete": 0}
    assert not SpecialArea.objects.filter(code="carrier-remote").exists()

    save_config(area, ctx=CTX)
    entry = AuditLog.objects.get(action="shipping.specialarea.created")
    assert entry.after is not None
    assert entry.after["postcode_patterns"] == area.postcode_patterns


def test_deleting_a_method_deletes_its_treatments_with_an_audit_entry() -> None:
    treatment = SpecialAreaTreatmentFactory.create()

    delete_config([treatment.method], ctx=CTX)

    assert not SpecialAreaTreatment.objects.exists()
    assert AuditLog.objects.filter(action="shipping.specialareatreatment.deleted").count() == 1


def test_an_area_a_method_treats_cannot_be_deleted() -> None:
    treatment = SpecialAreaTreatmentFactory.create()
    with pytest.raises(ProtectedError):
        delete_config([treatment.area], ctx=CTX)
    assert SpecialArea.objects.filter(pk=treatment.area.pk).exists()


def test_gr_70100_on_a_postcode_narrowed_island_zone_gets_a_priced_rate() -> None:
    # regression case 2: a vendor's own "remote islands" zone, priced by weight bands
    zone = ShippingZoneFactory.create(countries=["GR"], postcode_patterns=["70*"])
    method = ShippingMethodFactory.create(zone=zone, default=True)
    WeightBandFactory.create(method=method, from_kg=Decimal(0), to_kg=Decimal(5))

    decision = special_area_decision(method, Destination("GR", "70100"))

    assert decision is not None
    assert decision.area.code == "gr-crete"
    assert not decision.requests_quote
    assert not decision.is_indicative
    assert decision.cost(Decimal("9.90"), Decimal("2.000")) == Decimal("9.90")


def test_the_same_address_on_a_country_wide_zone_gets_the_default() -> None:
    zone = ShippingZoneFactory.create(countries=["GR"])
    method = ShippingMethodFactory.create(zone=zone, default=True)

    decision = special_area_decision(method, Destination("GR", "70100"))

    assert decision is not None
    assert decision.is_default
    assert decision.requests_quote


@pytest.mark.parametrize(
    ("zone_kwargs", "dest"),
    [
        # a regional zone by province (Campania): Capri is a minor island by postcode
        ({"countries": ["IT"], "states": ["NA", "SA"]}, Destination("IT", "80073", "NA")),
        # Livigno is an extra-VAT territory inside the province of Sondrio
        ({"countries": ["IT"], "states": ["SO", "MI"]}, Destination("IT", "23041", "SO")),
        # the whole country through broad prefixes
        (
            {"countries": ["IT"], "postcode_patterns": [f"{d}*" for d in range(10)]},
            Destination("IT", "07100"),
        ),
    ],
)
def test_a_zone_wider_than_the_area_keeps_the_default(
    zone_kwargs: dict[str, list[str]], dest: Destination
) -> None:
    zone = ShippingZoneFactory.create(**zone_kwargs)
    method = ShippingMethodFactory.create(zone=zone, default=True)

    decision = special_area_decision(method, dest)

    assert decision is not None
    assert decision.is_default
    assert decision.is_indicative
    assert decision.requests_quote


def test_a_country_qualified_area_matches_in_its_own_country_only() -> None:
    area = SpecialAreaFactory.create(
        code="pt-es-remote",
        kind=engine.REMOTE,
        countries=["PT", "ES"],
        postcode_patterns=["PT:9000..9999"],
        states=["ES:IB"],
    )
    snaps = {snap.code: snap for snap in special_area_snaps()}

    assert engine.area_match(snaps[area.code], Destination("PT", "9000-001")) is not None
    assert engine.area_match(snaps[area.code], Destination("ES", "90000")) is None
    assert engine.area_match(snaps[area.code], Destination("ES", "", "IB")) is not None
    assert engine.area_match(snaps[area.code], Destination("PT", "", "IB")) is None
