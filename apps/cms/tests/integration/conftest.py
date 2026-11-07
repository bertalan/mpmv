import pytest

from apps.cms.services import SeedReport, seed_site


@pytest.fixture
def seeded(db: None) -> SeedReport:
    """The sample home, About page and menus in every configured language."""
    return seed_site()
