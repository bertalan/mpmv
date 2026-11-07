"""ARC.12: factories create valid rows on the real database, with sensible variants."""

import itertools
import secrets

import pytest
from django.contrib.auth import authenticate

from apps.accounts.tests.factories import UserFactory
from apps.api.services import authenticate as authenticate_api_key
from apps.api.tests.factories import ApiClientFactory
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.vendors.tests.factories import VendorFactory
from tests.factory_registry import Factory, app_factories

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("factory_class", app_factories(), ids=lambda f: f.__name__)
def test_every_factory_creates_valid_distinct_rows(factory_class: Factory) -> None:
    first, second = factory_class.create_batch(2)
    assert first.pk is not None
    assert second.pk is not None
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
    vendor = VendorFactory.create(languages=["en", "it"])
    assert sorted(vendor.translations.values_list("language", flat=True)) == ["en", "it"]
    assert vendor.display_name.startswith("Vendor ")
    assert vendor.contact_email.endswith("@example.com")


def test_api_client_factory_exposes_a_working_runtime_key() -> None:
    client, key = ApiClientFactory.create_with_key(scopes=["catalog:write"])
    result = authenticate_api_key(f"Bearer {key}", ip=None, required_scope="catalog:write")
    assert result.ok
    assert result.client == client
