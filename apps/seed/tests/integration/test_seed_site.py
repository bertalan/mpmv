"""I18N.18: the seed syncs the default Wagtail Site to MPMV_BASE_URL (dry run writes nothing)."""

import pytest
from django.test.utils import override_settings
from wagtail.models import Site

from apps.seed.services import TEST_CONTEXT, seed

pytestmark = pytest.mark.django_db


def test_the_seed_syncs_the_default_site() -> None:
    Site.objects.filter(is_default_site=True).update(hostname="localhost", port=80)
    with override_settings(WAGTAILADMIN_BASE_URL="https://shop.example"):
        seed(ctx=TEST_CONTEXT, dry_run=True)
        assert Site.objects.get(is_default_site=True).hostname == "localhost"
        seed(ctx=TEST_CONTEXT)
    site = Site.objects.get(is_default_site=True)
    assert (site.hostname, site.port) == ("shop.example", 443)
