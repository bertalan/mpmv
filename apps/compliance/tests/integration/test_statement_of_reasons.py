"""CMP.5 part 3: acting on content after a notice or on staff's own initiative, the DSA article
17 statement of reasons, the transparency export (stored, never sent), and the article 18
notification to the authorities."""

import datetime as dt
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product, ProductStatus
from apps.catalog.tests.factories import ProductFactory
from apps.cms.tests.factories import LegalPageFactory
from apps.compliance import engine, services
from apps.compliance.models import Notice, StatementOfReasons, TransparencySubmission
from apps.compliance.tests.factories import (
    GpsrInfoFactory,
    NoticeFactory,
    VendorGpsrDefaultsFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")


@pytest.fixture(autouse=True)
def _operator() -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=timezone.now() - dt.timedelta(days=1)
    )


@pytest.fixture
def staff() -> Any:
    return UserFactory.create(
        platform_staff=True,
        permissions=["compliance.review_notices", "compliance.notify_authorities"],
    )


def terms_page() -> None:
    sections = [
        ("complaint_handling", "<p>Write to the complaints desk within six months.</p>"),
        ("redress", "<p>Pick a body.</p>"),
    ]
    page = LegalPageFactory.create(
        kind="platform_terms",
        body=[("legal_section", {"section": t, "heading": t, "text": x}) for t, x in sections],
    )
    page.save_revision().publish()


@pytest.fixture(autouse=True)
def _terms() -> None:
    terms_page()


def published_product() -> Product:
    product = ProductFactory.create(status=ProductStatus.PUBLISHED)
    return product


def issue(staff: Any, **override: Any) -> services.StatementChangeSet:
    product = override.pop("product", None) or published_product()
    args: dict[str, Any] = {
        "target_kind": "product",
        "target_ref": str(product.public_id),
        "scope": "The product is no longer shown or sold",
        "facts": "The listing offers a counterfeit item.",
        "transparency_explanation": "Counterfeit item offered.",
        "valid_until": None,
        "ground_kind": engine.GROUND_ILLEGAL,
        "ground": "Trade mark infringement",
        "automated_detection": False,
        "automated_decision": False,
        "ctx": CTX,
    }
    args.update(override)
    return services.issue_statement(staff, **args)


# --- Acting on the content


def test_an_action_notice_unpublishes_a_product_and_tells_the_vendor(staff: Any) -> None:
    notice = NoticeFactory.create(status=Notice.Status.ACTION_TAKEN)
    product = published_product()
    changes = issue(staff, product=product, notice_id=notice.public_id)
    statement = changes.statement
    assert statement is not None
    product.refresh_from_db()
    assert product.status == ProductStatus.DRAFT
    assert statement.vendor == product.vendor
    assert statement.notice == notice
    assert statement.restriction == engine.RESTRICTION_REMOVE
    assert statement.sent_at is not None
    mail = OutboxEmail.objects.get(template_key="compliance.statement_of_reasons")
    assert mail.recipient == product.vendor.contact_email
    assert AuditLog.objects.filter(action="compliance.statement.issued").exists()


def test_a_hide_of_an_event_carries_the_notice_reference(staff: Any) -> None:
    notice = NoticeFactory.create(
        status=Notice.Status.ACTION_TAKEN, target_kind=Notice.TargetKind.EVENT
    )
    event = EventPageFactory.create(live=True)
    changes = issue(
        staff, target_kind="event", target_ref=str(event.public_id), notice_id=notice.public_id
    )
    event = EventPage.objects.get(pk=event.pk)
    assert event.hidden_at is not None
    assert event.dsa_notice_ref == str(notice.public_id)
    assert changes.statement is not None
    assert changes.statement.target_kind == "event"


def test_a_storefront_restriction_suspends_the_vendor(staff: Any) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    changes = issue(
        staff,
        target_kind="vendor",
        target_ref=str(vendor.public_id),
        scope="The whole storefront",
    )
    vendor.refresh_from_db()
    assert vendor.status == Vendor.Status.SUSPENDED
    assert changes.statement is not None
    assert changes.statement.restriction == engine.RESTRICTION_SUSPEND
    assert changes.statement.notice is None


def test_a_notice_must_be_decided_as_action_taken(staff: Any) -> None:
    for status in (Notice.Status.RECEIVED, Notice.Status.UNDER_REVIEW, Notice.Status.NO_ACTION):
        notice = NoticeFactory.create(status=status)
        with pytest.raises(services.NoticeError) as excinfo:
            issue(staff, notice_id=notice.public_id)
        assert "notice" in excinfo.value.codes
    assert not StatementOfReasons.objects.exists()


def test_an_unknown_target_changes_nothing(staff: Any) -> None:
    import uuid

    with pytest.raises(services.NoticeError) as excinfo:
        issue(staff, target_ref=str(uuid.uuid4()))
    assert "target" in excinfo.value.codes
    with pytest.raises(services.NoticeError):
        issue(staff, target_kind="content")
    assert not StatementOfReasons.objects.exists()


def test_a_dry_run_changes_nothing_and_a_missing_part_is_refused(staff: Any) -> None:
    product = published_product()
    changes = issue(staff, product=product, dry_run=True)
    assert changes.changes
    product.refresh_from_db()
    assert product.status == ProductStatus.PUBLISHED
    assert not StatementOfReasons.objects.exists()
    assert not OutboxEmail.objects.exists()
    with pytest.raises(services.NoticeError) as excinfo:
        issue(staff, product=product, facts="", dry_run=True)
    assert "facts" in excinfo.value.codes


def test_only_platform_staff_act_on_content() -> None:
    owner = UserFactory.create()
    with pytest.raises(PermissionDenied):
        issue(owner)


# --- The statement itself


def test_the_statement_records_article_17_3_and_is_immutable(staff: Any) -> None:
    statement = issue(staff, automated_detection=True).statement
    assert statement is not None
    assert statement.automated_detection
    assert not statement.automated_decision
    assert statement.ground_kind == engine.GROUND_ILLEGAL
    statement.facts = "Changed afterwards"
    with pytest.raises(PermissionDenied):
        statement.save()
    with pytest.raises(PermissionDenied):
        statement.delete()
    assert StatementOfReasons.objects.get().facts != "Changed afterwards"


def test_the_redress_wording_comes_from_the_cms_terms_page(staff: Any) -> None:
    statement = issue(staff).statement
    assert statement is not None
    assert "complaints desk within six months" in statement.redress_text
    assert "Pick a body." in statement.redress_text
    mail = OutboxEmail.objects.get(template_key="compliance.statement_of_reasons")
    assert "complaints desk within six months" in mail.context


def test_without_redress_wording_nothing_is_sent(staff: Any) -> None:
    from apps.cms.models import LegalPage

    for page in LegalPage.objects.all():
        page.unpublish()
    product = published_product()
    with pytest.raises(services.NoticeError) as excinfo:
        issue(staff, product=product)
    assert "redress_text" in excinfo.value.codes
    product.refresh_from_db()
    assert product.status == ProductStatus.PUBLISHED
    assert not StatementOfReasons.objects.exists()


def test_the_email_gives_the_facts_and_the_notice_basis(staff: Any) -> None:
    notice = NoticeFactory.create(status=Notice.Status.ACTION_TAKEN)
    issue(staff, notice_id=notice.public_id)
    mail = OutboxEmail.objects.get(template_key="compliance.statement_of_reasons")
    assert "counterfeit" in mail.context
    assert str(notice.public_id) in mail.context
    assert "Trade mark infringement" in mail.context


def test_the_audit_entry_holds_ids_and_kinds_not_the_facts(staff: Any) -> None:
    issue(staff)
    entry = AuditLog.objects.get(action="compliance.statement.issued")
    text = str(entry.before) + str(entry.after)
    assert "counterfeit" not in text
    assert "restriction" in text


# --- The transparency database export (art. 24(5)): stored as pending, never sent


def test_a_pending_export_is_built_without_an_external_call(staff: Any) -> None:
    statement = issue(staff, automated_decision=False).statement
    assert statement is not None
    export = TransparencySubmission.objects.get(statement=statement)
    assert export.status == TransparencySubmission.Status.PENDING
    assert export.submitted_at is None
    assert export.payload["decision_visibility"] == "DECISION_VISIBILITY_CONTENT_REMOVED"
    assert export.payload["puid"] == str(statement.public_id)
    assert "contact_email" not in str(export.payload)


# --- Article 18: criminal offences


def test_staff_record_a_notification_to_the_authorities(staff: Any) -> None:
    notice = NoticeFactory.create(status=Notice.Status.UNDER_REVIEW)
    services.notify_authorities(
        staff,
        notice.public_id,
        authority="Public prosecutor, district X",
        note="Threat to life",
        ctx=CTX,
    )
    notice.refresh_from_db()
    assert notice.authorities_notified_at is not None
    assert notice.authorities_notified_to == "Public prosecutor, district X"
    entry = AuditLog.objects.get(action="compliance.notice.authorities_notified")
    assert "Threat to life" not in str(entry.after)


def test_the_notification_needs_an_authority_and_happens_once(staff: Any) -> None:
    notice = NoticeFactory.create()
    with pytest.raises(services.NoticeError) as excinfo:
        services.notify_authorities(staff, notice.public_id, authority=" ", note="x", ctx=CTX)
    assert "authority" in excinfo.value.codes
    services.notify_authorities(staff, notice.public_id, authority="Police", note="x", ctx=CTX)
    with pytest.raises(services.NoticeError):
        services.notify_authorities(staff, notice.public_id, authority="Police", note="x", ctx=CTX)


def test_a_notification_dry_run_changes_nothing(staff: Any) -> None:
    notice = NoticeFactory.create()
    changes = services.notify_authorities(
        staff, notice.public_id, authority="Police", note="x", ctx=CTX, dry_run=True
    )
    assert changes.changes
    notice.refresh_from_db()
    assert notice.authorities_notified_at is None


# --- Manager


def login(client: Client, user: object) -> None:
    verified_login(client, user)  # type: ignore[arg-type]


@pytest.fixture
def reviewer() -> object:
    return UserFactory.create(
        permissions=[
            "core.platform_owner",
            "compliance.review_notices",
            "compliance.notify_authorities",
        ],
        platform_staff=True,
    )


def test_a_reviewer_acts_on_the_content_from_the_notice(client: Client, reviewer: object) -> None:
    notice = NoticeFactory.create(status=Notice.Status.ACTION_TAKEN)
    product = published_product()
    login(client, reviewer)
    detail = f"/manager/platform/notices/{notice.public_id}/"
    assert "restrict" in client.get(detail).content.decode()
    response = client.post(
        detail,
        {
            "action": "restrict",
            "target_kind": "product",
            "target_ref": str(product.public_id),
            "scope": "Not shown or sold",
            "facts": "Counterfeit item",
            "transparency_explanation": "Counterfeit item offered.",
            "ground_kind": "illegal_content",
            "ground": "Trade mark infringement",
        },
    )
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.status == ProductStatus.DRAFT
    assert StatementOfReasons.objects.get().notice == notice
    assert "Counterfeit item" in client.get(detail).content.decode()


def test_a_reviewer_notifies_the_authorities(client: Client, reviewer: object) -> None:
    notice = NoticeFactory.create(status=Notice.Status.UNDER_REVIEW)
    login(client, reviewer)
    detail = f"/manager/platform/notices/{notice.public_id}/"
    response = client.post(
        detail, {"action": "authorities", "authority": "Police", "note": "Threat to life"}
    )
    assert response.status_code == 302
    notice.refresh_from_db()
    assert notice.authorities_notified_to == "Police"


def test_the_restriction_form_needs_the_review_permission(client: Client) -> None:
    notice = NoticeFactory.create(status=Notice.Status.ACTION_TAKEN)
    outsider = UserFactory.create(platform_staff=True)
    login(client, outsider)
    detail = f"/manager/platform/notices/{notice.public_id}/"
    assert client.post(detail, {"action": "authorities", "authority": "x"}).status_code in (
        403,
        404,
    )
    notice.refresh_from_db()
    assert notice.authorities_notified_at is None


# --- Review fixes: public text, DB immutability, restriction hold, permissions, cross-checks


def test_personal_data_never_reaches_the_public_payload(staff: Any) -> None:
    leak = "Ada ada@example.org https://shop.example.com/p/1"
    statement = issue(
        staff, facts=leak, ground=leak, scope=leak, transparency_explanation=leak
    ).statement
    assert statement is not None
    text = str(TransparencySubmission.objects.get(statement=statement).payload)
    assert "ada@example.org" not in text
    assert "shop.example.com" not in text
    assert "Ada" in statement.facts  # the vendor still gets the full facts


def test_the_public_explanation_is_required(staff: Any) -> None:
    with pytest.raises(services.NoticeError) as excinfo:
        issue(staff, transparency_explanation=" ")
    assert "transparency_explanation" in excinfo.value.codes


def test_the_duration_is_its_own_field(staff: Any) -> None:
    until = dt.date.today() + dt.timedelta(days=30)
    statement = issue(staff, valid_until=until).statement
    assert statement is not None
    assert statement.valid_until == until


def test_the_database_refuses_updates_and_deletes_of_a_statement(staff: Any) -> None:
    from django.db import DatabaseError, connection, transaction

    statement = issue(staff).statement
    assert statement is not None
    with pytest.raises(DatabaseError), transaction.atomic():
        StatementOfReasons.objects.filter(pk=statement.pk).update(facts="changed")
    with pytest.raises(DatabaseError), transaction.atomic():
        StatementOfReasons.objects.filter(pk=statement.pk).delete()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE compliance_statementofreasons SET scope = 'x'")
    assert StatementOfReasons.objects.get().facts != "changed"


def test_a_restricted_product_cannot_be_republished_until_staff_lift_it(staff: Any) -> None:
    from django.core.exceptions import ValidationError

    from apps.catalog.services import save_product

    product = published_product()
    VendorGpsrDefaultsFactory.create(vendor=product.vendor)
    GpsrInfoFactory.create(product=product)
    issue(staff, product=product)
    product.refresh_from_db()
    product.status = ProductStatus.PUBLISHED
    with pytest.raises(ValidationError) as excinfo:
        save_product(product, ctx=CTX)
    assert "restriction" in excinfo.value.message_dict
    services.lift_restriction(staff, product.public_id, reason="Appeal upheld", ctx=CTX)
    assert AuditLog.objects.filter(action="compliance.restriction.lifted").exists()
    product.refresh_from_db()
    product.status = ProductStatus.PUBLISHED
    save_product(product, ctx=CTX)
    with pytest.raises(services.NoticeError):
        services.lift_restriction(staff, product.public_id, reason="again", ctx=CTX)


def test_lifting_needs_a_reason_and_the_permission(staff: Any) -> None:
    product = published_product()
    issue(staff, product=product)
    with pytest.raises(services.NoticeError):
        services.lift_restriction(staff, product.public_id, reason=" ", ctx=CTX)
    plain = UserFactory.create(platform_staff=True)
    with pytest.raises(PermissionDenied):
        services.lift_restriction(plain, product.public_id, reason="x", ctx=CTX)


def test_staff_without_the_review_permission_cannot_restrict() -> None:
    plain = UserFactory.create(platform_staff=True)
    with pytest.raises(PermissionDenied):
        issue(plain)


def test_the_art_18_record_needs_its_own_permission() -> None:
    reviewer_only = UserFactory.create(
        platform_staff=True, permissions=["compliance.review_notices"]
    )
    notice = NoticeFactory.create()
    with pytest.raises(PermissionDenied):
        services.notify_authorities(
            reviewer_only, notice.public_id, authority="Police", note="x", ctx=CTX
        )
    notice.refresh_from_db()
    assert notice.authorities_notified_at is None


def test_the_notice_kind_must_match_the_target(staff: Any) -> None:
    notice = NoticeFactory.create(
        status=Notice.Status.ACTION_TAKEN, target_kind=Notice.TargetKind.EVENT
    )
    product = published_product()
    with pytest.raises(services.NoticeError) as excinfo:
        issue(staff, product=product, notice_id=notice.public_id)
    assert "notice" in excinfo.value.codes
    product.refresh_from_db()
    assert product.status == ProductStatus.PUBLISHED
