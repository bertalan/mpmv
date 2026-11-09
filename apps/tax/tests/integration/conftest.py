"""Tax integration tests start from an empty territory table.

Migration 0006 seeds the special territories (TAX.7); most tests create their own territories
with the codes the seed uses (`ES-CN`, `IT-LIVIGNO`), so the seed is removed before each test.
`test_special_territories_seed.py` tests the seed itself and keeps it.
"""

from collections.abc import Iterator

import pytest

from apps.tax.models import Territory


@pytest.fixture(autouse=True)
def _no_seeded_territories(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.module.__name__.endswith("test_special_territories_seed"):
        yield
        return
    request.getfixturevalue("db")
    Territory.objects.all().delete()
    yield
