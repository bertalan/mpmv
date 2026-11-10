"""SHP.14 guard: the extra-VAT special areas link to TAX.1 territories by code.

Territories are seeded by TAX.7 (tax migration 0006): a special area's `territory_code` must name a
seeded territory, which must belong to one of the area's
countries, so shipping and tax agree on where an address is. This test lives outside both apps
(they never import each other).
"""

import pytest

from apps.shipping import engine
from apps.shipping.models import SpecialArea
from apps.tax.models import Territory

pytestmark = pytest.mark.django_db


def test_every_seeded_extra_vat_area_names_a_valid_territory_code() -> None:
    areas = SpecialArea.objects.filter(kind=engine.EXTRA_VAT)
    assert areas.exists()
    for area in areas:
        territory = Territory(code=area.territory_code, name=area.name, country=area.countries[0])
        territory.clean_fields(exclude={"vat_status", "postcode_patterns"})


def test_every_extra_vat_area_links_to_a_seeded_territory_of_its_countries() -> None:
    territories = {t.code: t for t in Territory.objects.all()}
    areas = SpecialArea.objects.filter(kind=engine.EXTRA_VAT)
    assert areas.exists()
    for area in areas:
        assert area.territory_code in territories, area.code
        assert territories[area.territory_code].country in area.countries, area.code
