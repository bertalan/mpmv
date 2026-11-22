"""I18N.15: the demo seed stores its content under the configured languages only.

English is not mandatory: with `MPMV_LANGUAGES=it,de` the seed must create no "en" locale and no
"en" row. The module runs in both setups (the default languages and `it,de`) and checks the
invariant for whichever languages are configured. Idempotency is in `test_seed.py`.
"""

import pytest
from django.apps import apps
from wagtail.models import Locale

from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages, primary_language

pytestmark = pytest.mark.django_db

TRANSLATION_MODELS = (
    "catalog.CategoryTranslation",
    "catalog.TermTranslation",
    "catalog.AttributeTranslation",
    "catalog.AttributeValueTranslation",
    "catalog.BrandTranslation",
    "catalog.ProductTranslation",
    "vendors.VendorTranslation",
    "vendors.VendorContractTranslation",
    "shipping.CarrierTranslation",
)


def test_every_seeded_row_is_in_a_configured_language(demo_seed: ChangeSet) -> None:
    configured = set(configured_languages())
    for label in TRANSLATION_MODELS:
        stored = set(apps.get_model(label).objects.values_list("language", flat=True))
        assert stored, f"{label} has no seeded row"
        assert stored <= configured, f"{label}: {stored - configured} is not configured"


def test_no_locale_is_made_for_a_language_that_is_not_configured(demo_seed: ChangeSet) -> None:
    locales = set(Locale.objects.values_list("language_code", flat=True))
    assert locales <= set(configured_languages())
    assert Locale.get_default().language_code == primary_language()


def test_the_primary_language_has_every_translated_entity(demo_seed: ChangeSet) -> None:
    primary = primary_language()
    for label in ("catalog.ProductTranslation", "vendors.VendorTranslation"):
        assert apps.get_model(label).objects.filter(language=primary).exists()
