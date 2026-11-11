"""Tax integration tests start from an empty territory table.

Migrations 0006 and 0009 seed the special territories (TAX.7, D-28); most tests create their
own territories with the codes the seed uses (`ES-CN`, `IT-LIVIGNO`), so the seed is removed
before each test.
A test module that works on the seed itself sets `pytest.mark.keep_seeded_territories`.
Tests marked `django_db(transaction=True)` flush every table, the seed included.
"""

from collections.abc import Iterator

import pytest

from apps.tax.models import TaxRate, Territory


@pytest.fixture(autouse=True)
def _no_seeded_territories(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.get_closest_marker("keep_seeded_territories"):
        yield
        return
    request.getfixturevalue("db")
    TaxRate.objects.filter(territory__isnull=False).delete()  # the Madeira and Azores rates
    Territory.objects.all().delete()
    yield
