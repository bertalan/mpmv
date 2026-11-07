"""SHP.12: the migrations keep every SHP.1 rate, band and zone.

Each vendor-zone rate becomes the default shipping method of its vendor and zone (same row,
public id and bands); stored postcode ranges move to the `..` syntax; the country groups are
seeded.
"""

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core.countries import EU_MEMBER_STATES
from apps.shipping import engine
from apps.shipping.models import CountryGroup, ShippingMethod, ShippingZone
from apps.vendors.tests.factories import VendorFactory

BEFORE = [("shipping", "0001_initial")]


@pytest.mark.django_db(transaction=True)
def test_rates_become_default_methods_and_ranges_use_the_new_syntax() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old_apps = executor.loader.project_state(BEFORE).apps
    OldZone = old_apps.get_model("shipping", "ShippingZone")
    OldCarrier = old_apps.get_model("shipping", "Carrier")
    OldRate = old_apps.get_model("shipping", "VendorZoneRate")
    OldBand = old_apps.get_model("shipping", "WeightBand")

    vendor = VendorFactory.create()  # the vendors tables are not rolled back
    old_zone = OldZone.objects.create(
        code="gr-islands",
        name="GR islands",
        priority=10,
        countries=["GR"],
        postcode_patterns=["70000-74999", "846*", "9000-001", "00-950", "84600"],
    )
    carrier = OldCarrier.objects.create(code="main")
    rate = OldRate.objects.create(
        vendor_id=vendor.pk, zone=old_zone, carrier=carrier, over_max_weight="quote"
    )
    OldBand.objects.create(rate=rate, from_kg=0, to_kg=5, cost_net=Decimal("9.90"))
    OldBand.objects.create(rate=rate, from_kg=5, to_kg=None, cost_net=Decimal("14.90"))

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    method = ShippingMethod.objects.get()
    assert (method.pk, method.public_id, method.vendor_id) == (rate.pk, rate.public_id, vendor.pk)
    assert method.is_default is True
    assert (method.key, method.service_level) == ("standard", engine.STANDARD)
    assert method.tax_treatment == engine.FOLLOWS_GOODS
    assert method.over_max_weight == "quote"
    assert method.free_shipping_threshold is None
    assert [(b.from_kg, b.to_kg, b.cost_net) for b in method.bands.all()] == [
        (0, 5, Decimal("9.90")),
        (5, None, Decimal("14.90")),
    ]

    zone = ShippingZone.objects.get()
    assert zone.vendor is None
    assert zone.country_groups == []
    # Only the old "a-b" ranges (two numbers of equal length) change; PT and PL postcodes with
    # a dash stay exact postcodes.
    assert zone.postcode_patterns == ["70000..74999", "846*", "9000-001", "00-950", "84600"]
    zone.full_clean()  # every migrated pattern is valid in the new grammar

    assert set(CountryGroup.objects.get(code="eu").countries) == set(EU_MEMBER_STATES)


BEFORE_LOWER_CASE = [("shipping", "0005_seed_country_groups")]


@pytest.mark.django_db(transaction=True)
def test_country_group_codes_stored_in_capitals_are_lower_cased() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE_LOWER_CASE)
    old_apps = executor.loader.project_state(BEFORE_LOWER_CASE).apps
    OldGroup = old_apps.get_model("shipping", "CountryGroup")
    OldGroup.objects.create(code="DACH", countries=["DE", "AT", "CH"])

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    assert CountryGroup.objects.filter(code="dach").exists()
    assert not CountryGroup.objects.filter(code="DACH").exists()
