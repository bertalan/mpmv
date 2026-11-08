"""VEN.25 (owner decision MQ-5): the TAX.2 regime-change workflow applies the RF20 eligibility
rule of the vendor: established in an EU member state, with the `EX` number issued there.

Done when: a regime-change request to RF20 without the number is refused. The approval checks
again against the vendor as stored then, so a number removed or a seat moved since the request
blocks it too.
"""

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.tests.factories import utc
from apps.tax import services
from apps.tax.models import TaxRegimeChangeRequest
from apps.tax.tests.factories import TaxRegimeChangeRequestFactory
from apps.vendors.engine import CROSS_BORDER_FRANCHISE_REGIME
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="user:owner", source="vendor", ip="127.0.0.1")
STAFF_CTX = AuditContext(actor_id=2, actor_label="staff:ops", source="admin", ip="127.0.0.1")
PAST = utc(2026, 1)  # before the test clock: a fiscal regime change may apply at once
RF20 = CROSS_BORDER_FRANCHISE_REGIME


def eu_vendor(*, with_number: bool) -> Vendor:
    vendor: Vendor = VendorFactory.create(eu_company=True)
    if with_number:
        Vendor.objects.filter(pk=vendor.pk).update(
            cross_border_franchise_number=f"{vendor.vat_number}EX"
        )
        vendor.refresh_from_db()
    return vendor


def build_request(vendor: Vendor) -> TaxRegimeChangeRequest:
    owner = VendorMemberFactory.create(vendor=vendor).user
    request: TaxRegimeChangeRequest = TaxRegimeChangeRequestFactory.build(
        vendor=vendor,
        requested_by=owner,
        regime="domestic_only",
        oss_registered_since=None,
        fiscal_regime=RF20,
        effective_from=PAST,
    )
    return request


def test_a_request_to_rf20_without_the_number_is_refused() -> None:
    request = build_request(eu_vendor(with_number=False))
    with pytest.raises(ValidationError) as exc:
        services.request_regime_change(request, ctx=CTX)
    assert "EX" in " ".join(exc.value.messages)
    assert not TaxRegimeChangeRequest.objects.exists()


def test_a_dry_run_request_to_rf20_without_the_number_is_refused_too() -> None:
    request = build_request(eu_vendor(with_number=False))
    with pytest.raises(ValidationError):
        services.request_regime_change(request, ctx=CTX, dry_run=True)


def test_a_request_to_rf20_from_outside_the_eu_is_refused() -> None:
    vendor = VendorFactory.create(non_eu_company=True)
    Vendor.objects.filter(pk=vendor.pk).update(cross_border_franchise_number="DE123456789EX")
    request = build_request(Vendor.objects.get(pk=vendor.pk))
    with pytest.raises(ValidationError) as exc:
        services.request_regime_change(request, ctx=CTX)
    assert "EU member state" in " ".join(exc.value.messages)


def test_a_request_to_rf20_with_the_number_is_accepted_and_approved() -> None:
    request = build_request(eu_vendor(with_number=True))
    services.request_regime_change(request, ctx=CTX)

    reviewer = UserFactory.create(staff=True, permissions=[services.REVIEW_PERMISSION])
    services.approve_regime_change(request, reviewer, ctx=STAFF_CTX)
    assert Vendor.objects.get(pk=request.vendor.pk).fiscal_regime == RF20


def test_the_approval_is_refused_when_the_number_was_removed_since_the_request() -> None:
    request = build_request(eu_vendor(with_number=True))
    services.request_regime_change(request, ctx=CTX)
    Vendor.objects.filter(pk=request.vendor.pk).update(cross_border_franchise_number="")

    reviewer = UserFactory.create(staff=True, permissions=[services.REVIEW_PERMISSION])
    with pytest.raises(ValidationError):
        services.approve_regime_change(request, reviewer, ctx=STAFF_CTX)
    assert Vendor.objects.get(pk=request.vendor.pk).fiscal_regime != RF20
    assert TaxRegimeChangeRequest.objects.get(pk=request.pk).status == "pending"


def test_a_request_that_keeps_another_regime_needs_no_number() -> None:
    request = build_request(eu_vendor(with_number=False))
    request.fiscal_regime = ""
    request.regime = "oss"
    request.oss_registered_since = PAST.date()
    services.request_regime_change(request, ctx=CTX)
    assert TaxRegimeChangeRequest.objects.filter(pk=request.pk).exists()
