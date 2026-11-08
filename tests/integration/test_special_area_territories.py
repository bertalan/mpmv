"""SHP.14 guard: the extra-VAT special areas link to TAX.1 territories by code.

Territories are seeded by TAX.7; until then a special area's `territory_code` names the territory
it will link to. When a territory with that code exists, it must belong to one of the area's
countries, so shipping and tax agree on where an address is. This test lives outside both apps
(they never import each other).
"""

import pytest

from apps.shipping import engine
from apps.shipping.models import SpecialArea
from apps.tax.models import Territory
from apps.tax.tests.factories import TerritoryFactory

pytestmark = pytest.mark.django_db


def test_every_seeded_extra_vat_area_names_a_valid_territory_code() -> None:
    areas = SpecialArea.objects.filter(kind=engine.EXTRA_VAT)
    assert areas.exists()
    for area in areas:
        territory = Territory(code=area.territory_code, name=area.name, country=area.countries[0])
        territory.clean_fields(exclude={"vat_status", "postcode_patterns"})


def test_a_linked_territory_is_in_one_of_the_area_countries() -> None:
    TerritoryFactory.create(code="IT-LIVIGNO", country="IT", postcode_patterns=["23041"])
    TerritoryFactory.create(code="ES-CN", country="ES", postcode_patterns=["35*", "38*"])
    territories = {t.code: t for t in Territory.objects.all()}
    linked = SpecialArea.objects.filter(territory_code__in=territories)
    assert linked.count() == 2
    for area in linked:
        assert territories[area.territory_code].country in area.countries, area.code
