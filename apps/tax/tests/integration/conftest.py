"""Tax integration tests start from an empty territory table.

Migration 0006 seeds the special territories (TAX.7); most tests create their own territories
with the codes the seed uses (`ES-CN`, `IT-LIVIGNO`), so the seed is removed before each test.
A test module that works on the seed itself sets `pytest.mark.keep_seeded_territories`.
Tests marked `django_db(transaction=True)` flush every table, the seed included.
"""

from collections.abc import Iterator

import pytest

from apps.tax.models import Territory


@pytest.fixture(autouse=True)
def _no_seeded_territories(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.get_closest_marker("keep_seeded_territories"):
        yield
        return
    request.getfixturevalue("db")
    Territory.objects.all().delete()
    yield
