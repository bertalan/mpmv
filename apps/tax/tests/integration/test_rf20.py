"""VEN.18: fiscal regime RF20 (cross-border VAT franchise, Directive (EU) 2020/285) in the tax
app, against the real database.

Done when: the tax request constraint accepts RF01-RF20 except RF03; a regime-change request to
RF20 is accepted; an RF20 profile with an inconsistent VAT regime is reported. Until the tax
advisor gives the VAT mapping, every RF20 profile is also flagged for a manual review.
"""

import logging

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.tests.factories import utc
from apps.tax import services, tasks
from apps.tax.models import TaxRegimeChangeRequest
from apps.tax.tests.factories import TaxRegimeChangeRequestFactory, VendorTaxProfileFactory
from apps.vendors.engine import CROSS_BORDER_FRANCHISE_REGIME, FLAT_RATE_REGIME
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

CTX = AuditContext(actor_id=1, actor_label="user:owner", source="vendor", ip="127.0.0.1")
STAFF_CTX = AuditContext(actor_id=2, actor_label="staff:ops", source="admin", ip="127.0.0.1")
PAST = utc(2026, 1)  # before the test clock: a fiscal regime change may apply at once
RF20 = CROSS_BORDER_FRANCHISE_REGIME
ALL_REGIMES = [f"RF{n:02d}" for n in range(1, 21) if n != 3]

BEFORE = [("tax", "0003_territory_postcode_ranges")]
AFTER = [("tax", "0004_regime_request_fiscal_regime_rf20")]


def _request_to_rf20(vendor: Vendor) -> TaxRegimeChangeRequest:
    owner = VendorMemberFactory.create(vendor=vendor).user
    request: TaxRegimeChangeRequest = TaxRegimeChangeRequestFactory.build(
        vendor=vendor,
        requested_by=owner,
        regime="domestic_only",
        oss_registered_since=None,
        fiscal_regime=RF20,
        effective_from=PAST,
    )
    services.request_regime_change(request, ctx=CTX)
    return TaxRegimeChangeRequest.objects.get(pk=request.pk)


# --- Regime change requests (TAX.2)


@pytest.mark.django_db
def test_a_request_to_rf20_is_accepted_and_its_approval_declares_rf20() -> None:
    vendor = VendorFactory.create(fiscal_regime="RF01")
    request = _request_to_rf20(vendor)
    assert request.fiscal_regime == RF20

    reviewer = UserFactory.create(staff=True, permissions=[services.REVIEW_PERMISSION])
    services.approve_regime_change(request, reviewer, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.fiscal_regime == RF20
    assert services.current_tax_profile(vendor).regime == "domestic_only"


@pytest.mark.django_db
def test_a_flat_rate_vendor_may_move_to_rf20() -> None:
    current = VendorTaxProfileFactory.create(flat_rate=True, valid_from=utc(2025))
    request = _request_to_rf20(current.vendor)
    assert request.vendor.fiscal_regime == FLAT_RATE_REGIME


@pytest.mark.django_db
@pytest.mark.parametrize("regime", ALL_REGIMES)
def test_the_request_constraint_accepts_every_regime_of_the_specification(regime: str) -> None:
    request = TaxRegimeChangeRequestFactory.create()
    TaxRegimeChangeRequest.objects.filter(pk=request.pk).update(fiscal_regime=regime)
    assert TaxRegimeChangeRequest.objects.get(pk=request.pk).fiscal_regime == regime


@pytest.mark.django_db
@pytest.mark.parametrize("regime", ["RF03", "RF21"])
def test_the_request_constraint_refuses_regimes_outside_the_specification(regime: str) -> None:
    request = TaxRegimeChangeRequestFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRegimeChangeRequest.objects.filter(pk=request.pk).update(fiscal_regime=regime)


# --- Profile consistency


@pytest.mark.django_db
def test_an_rf20_vendor_may_have_a_tax_profile() -> None:
    profile = VendorTaxProfileFactory.create(vendor__fiscal_regime=RF20, valid_from=utc(2025))
    profile.full_clean()


@pytest.mark.django_db
def test_an_rf20_profile_with_an_inconsistent_vat_regime_is_reported() -> None:
    profile = VendorTaxProfileFactory.create(flat_rate=True, valid_from=utc(2025))
    Vendor.objects.filter(pk=profile.vendor.pk).update(fiscal_regime=RF20)

    [found] = services.inconsistent_tax_profiles()
    assert found.profile == profile
    assert set(found.errors) == {"exempt_reason"}
    assert "does not match the fiscal regime" in found.describe()


@pytest.mark.django_db
def test_every_rf20_profile_is_flagged_for_a_manual_review() -> None:
    VendorTaxProfileFactory.create(vendor__fiscal_regime="RF01", valid_from=utc(2025))
    profile = VendorTaxProfileFactory.create(vendor__fiscal_regime=RF20, valid_from=utc(2025))

    [found] = services.inconsistent_tax_profiles()
    assert found.profile == profile
    assert found.errors == {}
    assert set(found.review) == {"regime"}
    description = found.describe()
    assert f"{profile.vendor}: the tax profile needs a manual review" in description
    assert RF20 in description


@pytest.mark.django_db
def test_the_nightly_job_logs_rf20_profiles(caplog: pytest.LogCaptureFixture) -> None:
    profile = VendorTaxProfileFactory.create(vendor__fiscal_regime=RF20, valid_from=utc(2025))
    with caplog.at_level(logging.WARNING, logger="apps.tax"):
        tasks.check_profile_consistency.func(timestamp=0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(profile.vendor.public_id) in warnings[0].getMessage()
    assert RF20 in warnings[0].getMessage()


# --- Migration


@pytest.mark.django_db(transaction=True)
def test_the_migration_widens_the_request_constraint_and_is_reversible() -> None:
    executor = MigrationExecutor(connection)
    request = TaxRegimeChangeRequestFactory.create()
    TaxRegimeChangeRequest.objects.filter(pk=request.pk).update(fiscal_regime=RF20)

    # Back to RF01-RF19: refused while a request names RF20, then fine without one.
    with pytest.raises(IntegrityError):
        executor.migrate(BEFORE)
    TaxRegimeChangeRequest.objects.filter(pk=request.pk).update(fiscal_regime="RF19")
    executor.loader.build_graph()
    executor.migrate(BEFORE)
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxRegimeChangeRequest.objects.filter(pk=request.pk).update(fiscal_regime=RF20)

    executor.loader.build_graph()
    executor.migrate(AFTER)
    TaxRegimeChangeRequest.objects.filter(pk=request.pk).update(fiscal_regime=RF20)
    assert TaxRegimeChangeRequest.objects.get(pk=request.pk).fiscal_regime == RF20

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
