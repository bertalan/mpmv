"""ARC.12: factories create valid rows on the real database, with sensible variants."""

import itertools
import secrets

import pytest
from django.contrib.auth import authenticate

from apps.accounts.tests.factories import UserFactory
from apps.api.services import authenticate as authenticate_api_key
from apps.api.tests.factories import ApiClientFactory
from apps.core.i18n import primary_language
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.vendors.tests.factories import VendorFactory
from tests.factory_registry import Factory, app_factories
from tests.i18n import other_language

pytestmark = pytest.mark.django_db


# Factories of a table that holds one row by design (a database constraint): they return it.
SINGLETON_FACTORIES = {"FitmentSchemeFactory", "FooterSettingsFactory"}

# Page factories that take one free language each (a home page per locale, an events index under
# a home page): a configured set of two languages and the home page the migration creates leave
# room for one row of them, so the second is only asked for while a language is free (I18N.15).
LANGUAGE_BOUND_FACTORIES = {"HomePageFactory", "EventIndexPageFactory"}


def _free_languages(factory_name: str) -> int:
    from apps.cms.models import HomePage
    from apps.core.i18n import configured_languages
    from apps.events.models import EventIndexPage

    rows = HomePage if factory_name == "HomePageFactory" else EventIndexPage
    taken: int = rows.objects.count()
    return len(configured_languages()) - taken


@pytest.mark.parametrize("factory_class", app_factories(), ids=lambda f: f.__name__)
def test_every_factory_creates_valid_distinct_rows(factory_class: Factory) -> None:
    first = factory_class.create()
    assert first.pk is not None
    if (
        factory_class.__name__ in LANGUAGE_BOUND_FACTORIES
        and _free_languages(factory_class.__name__) < 1
    ):
        first.full_clean()  # one row per language: a two-language set has no room for a second
        return
    second = factory_class.create()
    assert second.pk is not None
    if factory_class.__name__ in SINGLETON_FACTORIES:
        assert first.pk == second.pk
    else:
        assert first.pk != second.pk
    first.full_clean()
    second.full_clean()


def test_user_factory_password_logs_in_and_traits_grant_staff_access() -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(password=password, superuser=True)
    assert authenticate(username=user.email, password=password) == user
    assert user.is_staff
    assert user.is_superuser


def test_user_factory_grants_the_requested_permissions() -> None:
    user = UserFactory.create(staff=True, permissions=["vendors.view_vendor"])
    assert user.is_staff
    assert not user.is_superuser
    assert user.has_perm("vendors.view_vendor")
    assert not user.has_perm("vendors.change_vendor")


def test_platform_operator_batches_have_adjacent_non_overlapping_periods() -> None:
    rows = PlatformOperatorFactory.create_batch(3)
    for earlier, later in itertools.pairwise(rows):
        assert earlier.valid_to == later.valid_from
    open_ended = PlatformOperatorFactory.create(valid_from=utc(2100), open_ended=True)
    assert open_ended.valid_to is None


def test_vendor_factory_creates_translations_per_language() -> None:
    vendor = VendorFactory.create(languages=[primary_language(), other_language()])
    assert sorted(vendor.translations.values_list("language", flat=True)) == sorted(
        [primary_language(), other_language()]
    )
    assert vendor.display_name.startswith("Vendor ")
    assert vendor.contact_email.endswith("@example.com")


def test_api_client_factory_exposes_a_working_runtime_key() -> None:
    client, key = ApiClientFactory.create_with_key(scopes=["catalog:write"])
    result = authenticate_api_key(f"Bearer {key}", ip=None, required_scope="catalog:write")
    assert result.ok
    assert result.client == client
