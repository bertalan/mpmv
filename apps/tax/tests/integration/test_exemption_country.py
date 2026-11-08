"""TAX.13 (owner decision MQ-3): an exemption reason is for VAT numbers of the country whose law
defines it. The rule holds on every entry point: a profile added by staff (service and admin),
a vendor request and its approval, and the consistency check that runs every night and when
the vendor's VAT number changes."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.tests.factories import utc
from apps.tax import engine, services
from apps.tax.models import VendorTaxProfile
from apps.tax.tests.factories import TaxRegimeChangeRequestFactory, VendorTaxProfileFactory
from apps.vendors.engine import FLAT_RATE_REGIME
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]
CTX = AuditContext(actor_id=2, actor_label="staff:ops", source="admin", ip="127.0.0.1")
GERMAN_VAT = "DE123456789"


def reviewer() -> User:
    user: User = UserFactory.create(staff=True, permissions=[services.REVIEW_PERMISSION])
    return user


def italian_vendor(**values: Any) -> Vendor:
    vendor: Vendor = VendorFactory.create(italian_company=True, **values)
    return vendor


def german_vendor(**values: Any) -> Vendor:
    vendor: Vendor = VendorFactory.create(eu_company=True, **values)
    return vendor


def staff_profile(vendor: Vendor, reason: str, **values: Any) -> VendorTaxProfile:
    profile: VendorTaxProfile = VendorTaxProfileFactory.build(
        vendor=vendor, regime=engine.EXEMPT, exempt_reason=reason, **values
    )
    services.configure_vendor_tax_profile(profile, approver=reviewer(), ctx=CTX)
    return profile


def refusal(call: Callable[[], Any]) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as caught:
        call()
    return caught.value.message_dict


# --- Staff path (configure_vendor_tax_profile)


def test_an_italian_vendor_saves_art74_publishing() -> None:
    vendor = italian_vendor()
    staff_profile(vendor, engine.ART74_PUBLISHING)
    assert VendorTaxProfile.objects.get(vendor=vendor).exempt_reason == engine.ART74_PUBLISHING


@pytest.mark.parametrize(
    ("reason", "fiscal_regime"),
    [(engine.ART74_PUBLISHING, "RF18"), (engine.FLAT_RATE_FORFETTARIO, FLAT_RATE_REGIME)],
)
def test_a_german_vendor_is_refused_the_italian_reasons(reason: str, fiscal_regime: str) -> None:
    vendor = german_vendor(fiscal_regime=fiscal_regime)
    errors = refusal(lambda: staff_profile(vendor, reason))
    assert set(errors) == {"exempt_reason"}
    assert not VendorTaxProfile.objects.exists()


def test_other_without_the_country_of_its_law_is_refused() -> None:
    vendor = german_vendor()
    errors = refusal(lambda: staff_profile(vendor, engine.OTHER_EXEMPTION))
    assert set(errors) == {"exempt_country"}


def test_other_stores_the_country_of_its_law() -> None:
    vendor = german_vendor()
    staff_profile(vendor, engine.OTHER_EXEMPTION, exempt_country="DE")
    assert VendorTaxProfile.objects.get(vendor=vendor).exempt_country == "DE"


def test_other_from_the_law_of_another_country_is_refused() -> None:
    vendor = german_vendor()
    errors = refusal(lambda: staff_profile(vendor, engine.OTHER_EXEMPTION, exempt_country="AT"))
    assert set(errors) == {"exempt_country"}


# --- Vendor request and approval (TAX.2)


def test_a_german_vendor_cannot_request_art74_publishing() -> None:
    vendor = german_vendor()
    owner = VendorMemberFactory.create(vendor=vendor).user
    request = TaxRegimeChangeRequestFactory.build(
        vendor=vendor,
        requested_by=owner,
        regime=engine.EXEMPT,
        exempt_reason=engine.ART74_PUBLISHING,
        oss_registered_since=None,
    )
    errors = refusal(lambda: services.request_regime_change(request, ctx=CTX))
    assert set(errors) == {"exempt_reason"}


def test_an_approved_request_gives_the_same_refusal_as_the_staff_path() -> None:
    """The VAT number moved to Germany after the request: the approval re-checks it."""
    vendor = italian_vendor()
    request = TaxRegimeChangeRequestFactory.create(
        vendor=vendor,
        regime=engine.EXEMPT,
        exempt_reason=engine.ART74_PUBLISHING,
        oss_registered_since=None,
    )
    Vendor.objects.filter(pk=vendor.pk).update(vat_number=GERMAN_VAT)
    vendor.refresh_from_db()

    approval = refusal(lambda: services.approve_regime_change(request, reviewer(), ctx=CTX))
    staff = refusal(lambda: staff_profile(vendor, engine.ART74_PUBLISHING))
    assert approval == staff
    request.refresh_from_db()
    assert request.status == engine.PENDING
    assert not VendorTaxProfile.objects.exists()


def test_an_approved_other_request_carries_the_country_of_its_law() -> None:
    vendor = german_vendor()
    request = TaxRegimeChangeRequestFactory.create(
        vendor=vendor,
        regime=engine.EXEMPT,
        exempt_reason=engine.OTHER_EXEMPTION,
        exempt_country="DE",
        oss_registered_since=None,
    )
    services.approve_regime_change(request, reviewer(), ctx=CTX)
    assert VendorTaxProfile.objects.get(vendor=vendor).exempt_country == "DE"


def test_a_request_for_other_without_a_country_is_refused() -> None:
    vendor = german_vendor()
    request = TaxRegimeChangeRequestFactory.build(
        vendor=vendor,
        regime=engine.EXEMPT,
        exempt_reason=engine.OTHER_EXEMPTION,
        oss_registered_since=None,
    )
    errors = refusal(request.full_clean)
    assert "exempt_country" in errors


# --- Admin (staff-added profile, approval page)


def profile_form(vendor: Vendor, **overrides: str) -> dict[str, str]:
    data = {
        "vendor": str(vendor.pk),
        "regime": "exempt",
        "exempt_reason": "art74_publishing",
        "exempt_country": "",
        "oss_registered_since": "",
        "valid_from_0": "2027-01-01",
        "valid_from_1": "00:00:00",
    }
    data.update(overrides)
    return data


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def test_the_admin_refuses_a_staff_added_profile_with_a_foreign_reason(
    staff_client: Client,
) -> None:
    vendor = german_vendor()
    response = staff_client.post(reverse("admin:tax_vendortaxprofile_add"), profile_form(vendor))
    assert response.status_code == 200
    assert "exempt_reason" in response.context["adminform"].form.errors
    assert not VendorTaxProfile.objects.exists()


def test_the_admin_saves_other_with_the_country_of_its_law(staff_client: Client) -> None:
    vendor = german_vendor()
    form = profile_form(vendor, exempt_reason="other", exempt_country="de")
    response = staff_client.post(reverse("admin:tax_vendortaxprofile_add"), form)
    assert response.status_code == 302, response.content
    assert VendorTaxProfile.objects.get().exempt_country == "DE"


def test_the_admin_approval_page_shows_the_refusal(staff_client: Client) -> None:
    vendor = italian_vendor()
    request = TaxRegimeChangeRequestFactory.create(
        vendor=vendor,
        regime=engine.EXEMPT,
        exempt_reason=engine.ART74_PUBLISHING,
        oss_registered_since=None,
    )
    Vendor.objects.filter(pk=vendor.pk).update(vat_number=GERMAN_VAT)
    url = reverse("admin:tax_taxregimechangerequest_approve", args=[request.pk])
    response = staff_client.post(url, {"reason": "Checked."})
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    request.refresh_from_db()
    assert request.status == engine.PENDING


# --- Consistency check: nightly, and when the VAT number changes


def warnings_logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_moving_an_exempt_italian_vendors_vat_number_abroad_is_reported(
    caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    profile = VendorTaxProfileFactory.create(
        vendor=italian_vendor(), art74=True, valid_from=utc(2025)
    )
    vendor = Vendor.objects.get(pk=profile.vendor.pk)
    vendor.vat_number = GERMAN_VAT
    with (
        caplog.at_level(logging.WARNING, logger="apps.tax"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        vendor.save()

    assert any(str(vendor.public_id) in message for message in warnings_logged(caplog))
    found = services.inconsistent_tax_profiles()
    assert [item.profile.pk for item in found] == [profile.pk]
    assert "exempt_reason" in found[0].errors


def test_the_vat_number_check_waits_for_the_commit(
    caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    profile = VendorTaxProfileFactory.create(
        vendor=italian_vendor(), art74=True, valid_from=utc(2025)
    )
    vendor = Vendor.objects.get(pk=profile.vendor.pk)
    vendor.vat_number = GERMAN_VAT
    with (
        caplog.at_level(logging.WARNING, logger="apps.tax"),
        django_capture_on_commit_callbacks() as callbacks,
    ):
        vendor.save()
        assert not warnings_logged(caplog)
    assert len(callbacks) == 1


def test_an_unrelated_vendor_change_reports_nothing(
    caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    profile = VendorTaxProfileFactory.create(
        vendor=italian_vendor(), art74=True, valid_from=utc(2025)
    )
    vendor = Vendor.objects.get(pk=profile.vendor.pk)
    vendor.display_name = "Renamed"
    with (
        caplog.at_level(logging.WARNING, logger="apps.tax"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        vendor.save()
    assert not warnings_logged(caplog)
    assert services.inconsistent_tax_profiles() == []


def test_a_display_name_save_of_an_inconsistent_vendor_reports_nothing_again(
    caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    """Already flagged (nightly job, tax admin): saving another field does not log it again."""
    profile = VendorTaxProfileFactory.create(
        vendor=italian_vendor(), art74=True, valid_from=utc(2025)
    )
    Vendor.objects.filter(pk=profile.vendor.pk).update(vat_number=GERMAN_VAT)
    vendor = Vendor.objects.get(pk=profile.vendor.pk)
    vendor.display_name = "Renamed"
    with (
        caplog.at_level(logging.WARNING, logger="apps.tax"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        vendor.save()
    assert not warnings_logged(caplog)
    assert len(services.inconsistent_tax_profiles()) == 1


def test_a_flat_rate_approval_reports_nothing(
    caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    """The approval saves the vendor (RF19) before adding the flat-rate profile: the check must
    not compare the new fiscal regime with the profile being replaced."""
    VendorTaxProfileFactory.create(
        vendor=italian_vendor(fiscal_regime="RF01"),
        regime=engine.DOMESTIC_ONLY,
        oss_registered_since=None,
        valid_from=utc(2025),
    )
    vendor = Vendor.objects.get()
    request = TaxRegimeChangeRequestFactory.create(
        vendor=vendor,
        regime=engine.EXEMPT,
        exempt_reason=engine.FLAT_RATE_FORFETTARIO,
        oss_registered_since=None,
        effective_from=utc(2026, 1),
    )
    with (
        caplog.at_level(logging.WARNING, logger="apps.tax"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        services.approve_regime_change(request, reviewer(), ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == FLAT_RATE_REGIME
    assert not warnings_logged(caplog)
    assert services.inconsistent_tax_profiles() == []


def test_moving_the_country_of_a_vat_number_without_prefix_is_reported(
    caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    """A VAT number stored without its prefix belongs to the vendor's country."""
    profile = VendorTaxProfileFactory.create(
        vendor=italian_vendor(), art74=True, valid_from=utc(2025)
    )
    Vendor.objects.filter(pk=profile.vendor.pk).update(vat_number="123456789")
    vendor = Vendor.objects.get(pk=profile.vendor.pk)
    vendor.country = "DE"
    with (
        caplog.at_level(logging.WARNING, logger="apps.tax"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        vendor.save()
    assert any(str(vendor.public_id) in message for message in warnings_logged(caplog))
