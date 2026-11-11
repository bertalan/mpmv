"""CMP.5 part 4: the 30 days' notice before a business user's service is terminated (P2B art. 4).

Frozen clock: the services take `now`, so the 30 days are tested to the second, not by waiting.
"""

import datetime as dt
import json
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import engine, services, tasks
from apps.compliance.models import StatementOfReasons, TerminationNotice, TransparencySubmission
from apps.compliance.tests.factories import TerminationNoticeFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory
from apps.orders import services as order_services
from apps.orders.tests.factories import paid_vendor_order
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

# The CORE.18 catalog role group (apps/manager/roles.py); not imported: compliance sits below.
CATALOG_GROUP = "Platform: catalog and moderation"

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
NOW = dt.datetime(2026, 3, 1, 10, 0, tzinfo=dt.UTC)
DAY30 = NOW + dt.timedelta(days=30)


@pytest.fixture(autouse=True)
def _operator() -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=timezone.now() - dt.timedelta(days=1)
    )


@pytest.fixture
def staff() -> Any:
    return UserFactory.create(
        platform_staff=True,
        permissions=["core.platform_owner", "compliance.review_notices"],
    )


@pytest.fixture
def catalog_reviewer() -> Any:
    """Catalog staff read the terminations (CORE.18 matrix: R) but never write them."""
    user = UserFactory.create(
        platform_staff=True, is_staff=True, permissions=["compliance.review_notices"]
    )
    user.groups.add(Group.objects.get(name=CATALOG_GROUP))
    return user


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def issue(staff: Any, vendor: Vendor, **override: Any) -> services.TerminationChangeSet:
    args: dict[str, Any] = {
        "facts": "The storefront keeps selling goods the terms forbid.",
        "ground": "Platform terms, prohibited goods",
        "now": NOW,
        "ctx": CTX,
    }
    args.update(override)
    return services.issue_termination_notice(staff, str(vendor.public_id), **args)


# --- Issuing the notice


def test_a_notice_records_the_effective_time_thirty_days_ahead(staff: Any, vendor: Vendor) -> None:
    changes = issue(staff, vendor)
    notice = changes.notice
    assert notice is not None
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING
    assert notice.issued_at == NOW
    assert notice.effective_at == DAY30
    assert notice.notice_days == 30
    assert notice.exception == ""
    assert notice.vendor == vendor
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE  # the service goes on during the notice
    entry = AuditLog.objects.get(action="compliance.termination.issued")
    assert str(vendor.public_id) in json.dumps(entry.after)
    assert "forbid" not in json.dumps(entry.after)  # the facts stay in the model, not the log


def test_the_vendor_is_sent_the_notice_on_a_durable_medium(staff: Any, vendor: Vendor) -> None:
    issue(staff, vendor)
    mail = OutboxEmail.objects.get(template_key="compliance.termination_notice")
    assert mail.recipient == vendor.contact_email
    assert mail.language == "en"


def test_the_vendor_language_is_the_owner_language_with_english_fallback(
    staff: Any, vendor: Vendor
) -> None:
    VendorMemberFactory.create(vendor=vendor, role="owner", user__preferred_language="it")
    issue(staff, vendor)
    assert OutboxEmail.objects.get(template_key="compliance.termination_notice").language == "it"


def test_a_vendor_without_an_owner_gets_english(staff: Any, vendor: Vendor) -> None:
    assert services.vendor_language(vendor) == "en"


def test_a_configured_longer_period_applies_and_a_shorter_one_never(
    staff: Any, vendor: Vendor, settings: Any
) -> None:
    settings.TERMINATION_NOTICE_DAYS = 45
    assert issue(staff, vendor).notice.effective_at == NOW + dt.timedelta(days=45)  # type: ignore[union-attr]
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    settings.TERMINATION_NOTICE_DAYS = 7
    assert issue(staff, other).notice.effective_at == DAY30  # type: ignore[union-attr]


def test_a_chosen_date_before_thirty_days_is_refused(staff: Any, vendor: Vendor) -> None:
    early = DAY30 - dt.timedelta(minutes=1)
    with pytest.raises(services.NoticeError) as error:
        issue(staff, vendor, effective_at=early)
    assert error.value.args == ("effective_at",)
    assert not TerminationNotice.objects.exists()
    assert not OutboxEmail.objects.exists()


def test_a_later_date_is_accepted(staff: Any, vendor: Vendor) -> None:
    later = NOW + dt.timedelta(days=60)
    assert issue(staff, vendor, effective_at=later).notice.effective_at == later  # type: ignore[union-attr]


@pytest.mark.parametrize("exception", engine.TERMINATION_EXCEPTIONS)
def test_an_exception_from_the_closed_list_allows_a_shorter_notice(
    staff: Any, vendor: Vendor, exception: str
) -> None:
    changes = issue(staff, vendor, exception=exception, effective_at=NOW + dt.timedelta(days=2))
    assert changes.notice is not None
    assert changes.notice.exception == exception
    assert changes.notice.notice_days == 2


def test_an_exception_without_a_date_ends_the_service_now(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor, exception="illegal_content").notice
    assert notice is not None
    assert notice.effective_at == NOW


def test_a_made_up_exception_gives_no_shorter_notice(staff: Any, vendor: Vendor) -> None:
    with pytest.raises(services.NoticeError):
        issue(staff, vendor, exception="we_felt_like_it", effective_at=NOW)


def test_the_database_refuses_a_short_notice_without_an_exception(vendor: Vendor) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TerminationNoticeFactory.create(vendor=vendor, effective_at=DAY30 - dt.timedelta(days=1))


def test_the_database_allows_a_short_notice_with_an_exception(vendor: Vendor) -> None:
    TerminationNoticeFactory.create(
        vendor=vendor,
        exception="legal_obligation",
        issued_at=NOW,
        effective_at=NOW + dt.timedelta(days=1),
    )


def test_facts_and_ground_are_required(staff: Any, vendor: Vendor) -> None:
    with pytest.raises(services.NoticeError):
        issue(staff, vendor, facts=" ")
    with pytest.raises(services.NoticeError):
        issue(staff, vendor, ground="")


def test_only_review_staff_may_issue(vendor: Vendor) -> None:
    with pytest.raises(PermissionDenied):
        issue(UserFactory.create(platform_staff=True), vendor)
    with pytest.raises(PermissionDenied):
        issue(UserFactory.create(permissions=["compliance.review_notices"]), vendor)


def test_a_vendor_that_is_not_in_service_or_unknown_is_refused(staff: Any) -> None:
    closed = VendorFactory.create(status=Vendor.Status.CLOSED)
    with pytest.raises(services.NoticeError):
        issue(staff, closed)
    applicant = VendorFactory.create(status=Vendor.Status.APPLICANT)
    with pytest.raises(services.NoticeError):
        issue(staff, applicant)
    with pytest.raises(services.NoticeError):
        services.issue_termination_notice(
            staff,
            "00000000-0000-0000-0000-000000000000",
            facts="x",
            ground="y",
            now=NOW,
            ctx=CTX,
        )


def test_a_suspended_vendor_can_be_given_notice(staff: Any) -> None:
    suspended = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    assert issue(staff, suspended).notice is not None


def test_a_vendor_has_one_pending_notice_at_a_time(staff: Any, vendor: Vendor) -> None:
    issue(staff, vendor)
    with pytest.raises(services.NoticeError) as error:
        issue(staff, vendor)
    assert error.value.args == ("pending",)


def test_a_dry_run_reports_the_notice_and_changes_nothing(staff: Any, vendor: Vendor) -> None:
    changes = issue(staff, vendor, dry_run=True)
    assert changes.notice is not None
    assert changes.changes
    assert not TerminationNotice.objects.exists()
    assert not OutboxEmail.objects.exists()
    assert not AuditLog.objects.filter(action="compliance.termination.issued").exists()


def test_a_notice_is_never_deleted(vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    with pytest.raises(PermissionDenied):
        notice.delete()


# --- Withdrawing the notice


def test_staff_withdraw_a_pending_notice_and_the_vendor_is_told(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="The infringement was corrected.", now=NOW, ctx=CTX
    )
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.WITHDRAWN
    assert notice.withdrawn_at == NOW
    assert notice.withdraw_reason == "The infringement was corrected."
    assert OutboxEmail.objects.filter(template_key="compliance.termination_withdrawn").exists()
    assert AuditLog.objects.filter(action="compliance.termination.withdrawn").exists()
    # A new notice can be given later.
    assert issue(staff, vendor).notice is not None


def test_a_withdrawn_notice_is_never_applied(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="Resolved.", now=NOW, ctx=CTX
    )
    services.apply_due_terminations(now=DAY30 + dt.timedelta(days=5), ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE


def test_only_a_pending_notice_can_be_withdrawn_and_a_reason_is_needed(
    staff: Any, vendor: Vendor
) -> None:
    applied = TerminationNoticeFactory.create(
        vendor=vendor, status=TerminationNotice.Status.APPLIED
    )
    with pytest.raises(services.NoticeError):
        services.withdraw_termination_notice(staff, applied.public_id, reason="x", ctx=CTX)
    pending = TerminationNoticeFactory.create(vendor=VendorFactory.create(status="active"))
    with pytest.raises(services.NoticeError):
        services.withdraw_termination_notice(staff, pending.public_id, reason=" ", ctx=CTX)
    with pytest.raises(PermissionDenied):
        services.withdraw_termination_notice(
            UserFactory.create(platform_staff=True), pending.public_id, reason="x", ctx=CTX
        )


def test_withdrawing_dry_run_changes_nothing(staff: Any, vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="Resolved.", ctx=CTX, dry_run=True
    )
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING
    assert not OutboxEmail.objects.exists()


# --- Applying the termination on the effective date, never earlier


def test_the_termination_is_not_applied_one_second_early(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    changes = services.apply_due_terminations(now=DAY30 - dt.timedelta(seconds=1), ctx=CTX)
    assert changes.changes == []
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING


def test_the_vendor_is_closed_through_the_closure_service_at_the_effective_time(
    staff: Any, vendor: Vendor
) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.APPLIED
    assert notice.applied_at == DAY30
    assert AuditLog.objects.filter(action="compliance.termination.applied").exists()
    # The vendor closure service wrote its own status entry.
    assert AuditLog.objects.filter(action="vendor.status_changed").exists()


def test_applying_twice_changes_nothing_the_second_time(staff: Any, vendor: Vendor) -> None:
    issue(staff, vendor)
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    assert services.apply_due_terminations(now=DAY30, ctx=CTX).changes == []


def test_a_suspended_vendor_is_closed_too(staff: Any) -> None:
    suspended = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    issue(staff, suspended)
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    suspended.refresh_from_db()
    assert suspended.status == Vendor.Status.CLOSED


def test_a_vendor_already_closed_just_marks_the_notice_applied(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.CLOSED)
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.APPLIED


def test_the_scheduled_job_applies_what_is_due(
    staff: Any, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue(staff, vendor)
    monkeypatch.setattr(timezone, "now", lambda: DAY30)
    tasks.apply_due_terminations(0)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED


def test_an_exception_notice_is_applied_when_its_short_date_arrives(
    staff: Any, vendor: Vendor
) -> None:
    issue(staff, vendor, exception="illegal_content", effective_at=NOW + dt.timedelta(days=2))
    services.apply_due_terminations(now=NOW + dt.timedelta(days=1), ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.ACTIVE
    services.apply_due_terminations(now=NOW + dt.timedelta(days=2), ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.CLOSED


# --- During the notice: the vendor sees the reason and can take its data


def test_the_vendor_sees_its_pending_notice_with_the_reason(staff: Any, vendor: Vendor) -> None:
    issue(staff, vendor)
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    notice = services.pending_termination(vendor)
    assert notice is not None
    assert "forbid" in notice.facts
    assert services.pending_termination(other) is None


def test_the_data_bundle_holds_the_vendor_data_and_the_notice(staff: Any, vendor: Vendor) -> None:
    mine = ProductFactory.create(vendor=vendor)
    theirs = ProductFactory.create()
    issue(staff, vendor)
    bundle = json.loads("".join(services.vendor_data_chunks(vendor, ctx=CTX)))
    text = json.dumps(bundle)
    assert mine.sku in text
    assert theirs.sku not in text
    assert bundle["termination_notice"]["effective_at"] == DAY30.isoformat()
    assert "forbid" in bundle["termination_notice"]["facts"]
    assert AuditLog.objects.filter(action="compliance.vendor_data.downloaded").exists()


def test_the_data_is_available_only_while_a_notice_is_pending(vendor: Vendor) -> None:
    with pytest.raises(services.NoticeError) as error:
        services.vendor_data_chunks(vendor, ctx=CTX)
    assert error.value.args == ("pending",)


def test_the_data_download_is_rate_limited_per_vendor(vendor: Vendor) -> None:
    TerminationNoticeFactory.create(vendor=vendor)
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    TerminationNoticeFactory.create(vendor=other)
    for _ in range(services.DATA_DOWNLOADS_PER_HOUR):
        assert services.data_download_wait(vendor) == dt.timedelta(0)
    assert services.data_download_wait(vendor) > dt.timedelta(0)
    assert services.data_download_wait(other) == dt.timedelta(0)


def test_the_data_is_streamed_in_chunks(vendor: Vendor) -> None:
    TerminationNoticeFactory.create(vendor=vendor)
    chunks = services.vendor_data_chunks(vendor, ctx=CTX)
    assert not isinstance(chunks, (list, dict, str))
    assert json.loads("".join(chunks))["vendor"]["display_name"] == vendor.display_name


# --- Review fixes: due notices, roles, the database floor


def test_a_notice_that_is_already_due_cannot_be_withdrawn(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    before = DAY30 - dt.timedelta(seconds=1)
    with pytest.raises(services.NoticeError) as error:
        services.withdraw_termination_notice(
            staff, notice.public_id, reason="Late.", now=DAY30, ctx=CTX
        )
    assert error.value.args == ("due",)
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING
    # One second earlier it still works.
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="On time.", now=before, ctx=CTX
    )


def test_only_the_platform_owner_issues_and_withdraws(
    staff: Any, catalog_reviewer: Any, vendor: Vendor
) -> None:
    with pytest.raises(PermissionDenied):
        issue(catalog_reviewer, vendor)
    notice = issue(staff, vendor).notice
    assert notice is not None
    with pytest.raises(PermissionDenied):
        services.withdraw_termination_notice(
            catalog_reviewer, notice.public_id, reason="x", ctx=CTX
        )
    assert services.can_read_terminations(catalog_reviewer)
    assert not services.can_write_terminations(catalog_reviewer)
    assert services.can_write_terminations(staff)


def test_the_database_refuses_an_end_before_the_notice_even_with_an_exception(
    vendor: Vendor,
) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TerminationNoticeFactory.create(
            vendor=vendor,
            exception="legal_obligation",
            effective_at=NOW - dt.timedelta(days=1),
            issued_at=NOW,
        )


# --- Statement of reasons and transparency record (CMP.5 3/4, owner decision Q6)


def test_a_notice_also_stores_the_statement_of_reasons_and_the_transparency_record(
    staff: Any, vendor: Vendor
) -> None:
    notice = issue(staff, vendor, transparency_explanation="Seller account terminated.").notice
    assert notice is not None
    notice.refresh_from_db()
    statement = notice.statement
    assert isinstance(statement, StatementOfReasons)
    assert statement.vendor == vendor
    assert statement.restriction == engine.RESTRICTION_TERMINATE
    assert statement.target_kind == "vendor"
    assert statement.target_ref == vendor.public_id
    assert statement.facts == notice.facts
    assert statement.ground_kind == engine.GROUND_TERMS
    record = TransparencySubmission.objects.get(statement=statement)
    assert record.status == TransparencySubmission.Status.PENDING
    assert record.payload["decision_account"] == "DECISION_ACCOUNT_TERMINATED"
    assert record.payload["puid"] == str(statement.public_id)
    assert AuditLog.objects.filter(action="compliance.statement.issued").exists()


def test_an_illegal_content_exception_makes_the_statement_an_illegal_content_one(
    staff: Any, vendor: Vendor
) -> None:
    notice = issue(staff, vendor, exception="illegal_content").notice
    assert notice is not None
    notice.refresh_from_db()
    assert notice.statement.ground_kind == engine.GROUND_ILLEGAL  # type: ignore[union-attr]


def test_a_dry_run_stores_no_statement(staff: Any, vendor: Vendor) -> None:
    issue(staff, vendor, dry_run=True)
    assert not StatementOfReasons.objects.exists()
    assert not TransparencySubmission.objects.exists()


# --- At the closure date the open orders and the payouts are frozen (owner decision Q2)


def test_closing_by_termination_freezes_the_open_orders_and_the_payouts(
    staff: Any, vendor: Vendor
) -> None:
    sub = paid_vendor_order(vendor)
    notice = issue(staff, vendor).notice
    assert notice is not None
    assert not services.payouts_frozen(vendor)
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    notice.refresh_from_db()
    assert notice.frozen_at == DAY30
    assert notice.frozen_orders == 1
    assert services.payouts_frozen(vendor)
    with pytest.raises(ValueError, match="frozen"):
        order_services.transition_vendor_order(sub, "accepted", ctx=CTX)
    sub.refresh_from_db()
    assert sub.state == "pending"
    assert services.orders_frozen(vendor)


def test_a_voluntarily_closed_vendor_can_still_move_its_placed_orders(vendor: Vendor) -> None:
    sub = paid_vendor_order(vendor)
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.CLOSED)
    vendor.refresh_from_db()
    assert not services.orders_frozen(vendor)
    order_services.transition_vendor_order(sub, "accepted", ctx=CTX)
    sub.refresh_from_db()
    assert sub.state == "accepted"


def test_the_review_releases_the_frozen_orders(staff: Any, vendor: Vendor) -> None:
    sub = paid_vendor_order(vendor)
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    services.review_termination_freeze(staff, notice.public_id, note="Settled.", ctx=CTX)
    assert not services.orders_frozen(vendor)
    order_services.transition_vendor_order(sub, "accepted", ctx=CTX)


def test_staff_review_ends_the_payout_freeze_and_only_the_owner_may(
    staff: Any, catalog_reviewer: Any, vendor: Vendor
) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    with pytest.raises(PermissionDenied):
        services.review_termination_freeze(catalog_reviewer, notice.public_id, note="ok", ctx=CTX)
    services.review_termination_freeze(
        staff, notice.public_id, note="Orders settled by hand.", now=DAY30, ctx=CTX
    )
    notice.refresh_from_db()
    assert notice.freeze_reviewed_at == DAY30
    assert not services.payouts_frozen(vendor)
    assert AuditLog.objects.filter(action="compliance.termination.freeze_reviewed").exists()
    with pytest.raises(services.NoticeError):  # a second review
        services.review_termination_freeze(staff, notice.public_id, note="again", ctx=CTX)


def test_a_freeze_review_needs_an_applied_notice_and_a_note(staff: Any, vendor: Vendor) -> None:
    pending = issue(staff, vendor).notice
    assert pending is not None
    with pytest.raises(services.NoticeError):
        services.review_termination_freeze(staff, pending.public_id, note="x", ctx=CTX)
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    with pytest.raises(services.NoticeError):
        services.review_termination_freeze(staff, pending.public_id, note=" ", ctx=CTX)


# --- Withdrawal voids the transparency record; the date is the effective date


def test_withdrawing_voids_the_pending_transparency_record(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    notice.refresh_from_db()
    record = TransparencySubmission.objects.get(statement=notice.statement)
    assert record in services.transparency_to_submit()
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="The infringement was corrected.", now=NOW, ctx=CTX
    )
    record.refresh_from_db()
    assert record.status == TransparencySubmission.Status.VOIDED
    assert record.voided_at == NOW
    assert record.void_reason == "The infringement was corrected."
    assert record not in services.transparency_to_submit()
    entry = AuditLog.objects.get(action="compliance.transparency_voided")
    assert str(notice.statement.public_id) in json.dumps(entry.after)  # type: ignore[union-attr]


def test_a_voided_record_is_never_picked_up_even_next_to_pending_ones(
    staff: Any, vendor: Vendor
) -> None:
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    issue(staff, other)
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="Resolved.", now=NOW, ctx=CTX
    )
    pending = list(services.transparency_to_submit())
    assert len(pending) == 1
    assert pending[0].statement.vendor == other


def test_a_withdrawal_dry_run_voids_nothing(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    services.withdraw_termination_notice(
        staff, notice.public_id, reason="x", now=NOW, ctx=CTX, dry_run=True
    )
    assert TransparencySubmission.objects.get().status == TransparencySubmission.Status.PENDING


def test_the_transparency_date_is_the_effective_date(staff: Any, vendor: Vendor) -> None:
    notice = issue(staff, vendor).notice
    assert notice is not None
    notice.refresh_from_db()
    payload = TransparencySubmission.objects.get(statement=notice.statement).payload
    assert payload["application_date"] == timezone.localdate(DAY30).isoformat()


def test_a_frozen_order_move_is_a_domain_error(staff: Any, vendor: Vendor) -> None:
    sub = paid_vendor_order(vendor)
    issue(staff, vendor)
    services.apply_due_terminations(now=DAY30, ctx=CTX)
    with pytest.raises(order_services.OrdersFrozen):
        order_services.transition_vendor_order(sub, "accepted", ctx=CTX)
