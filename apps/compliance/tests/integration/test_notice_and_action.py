"""CMP.5 part 2: DSA article 16 notice and action: the public form, the status workflow with its
audit trail and emails, and the Manager platform-space list and detail."""

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.captcha import TOKEN_FIELD
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.fakes import FakeCaptchaVerifier
from apps.accounts.tests.mfa import verified_login
from apps.compliance import engine, services
from apps.compliance.models import Notice
from apps.compliance.tests.factories import NoticeFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail, Throttle
from apps.core.tests.factories import PlatformOperatorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
IP = "203.0.113.7"


@pytest.fixture(autouse=True)
def _operator() -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=timezone.now() - dt.timedelta(days=1)
    )


@pytest.fixture(autouse=True)
def _reset_captcha() -> Iterator[None]:
    FakeCaptchaVerifier.reset()
    yield
    FakeCaptchaVerifier.reset()


def form_data(**extra: str) -> dict[str, str]:
    data = {
        "target_kind": "product",
        "url": "https://shop.example.com/products/thing/",
        "explanation": "The item is a counterfeit.",
        "category": "general",
        "notifier_name": "Ada Reporter",
        "notifier_email": "ada@example.org",
        "good_faith": "on",
    }
    data.update(extra)
    return data


def url() -> str:
    return reverse("dsa_notice")


# --- Public form


def test_the_form_is_public_and_prefilled_from_the_report_link(client: Client) -> None:
    response = client.get(url(), {"url": "https://shop.example.com/events/x/", "kind": "event"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "https://shop.example.com/events/x/" in html
    assert 'name="good_faith"' in html


def test_a_valid_submission_stores_a_notice_and_confirms_receipt(client: Client) -> None:
    response = client.post(url(), form_data(), REMOTE_ADDR=IP)
    assert response.status_code == 302
    notice = Notice.objects.get()
    assert notice.status == Notice.Status.RECEIVED
    assert notice.notifier_email == "ada@example.org"
    receipt = OutboxEmail.objects.get(recipient="ada@example.org")
    assert receipt.template_key == "compliance.notice_received"
    done = client.get(response["Location"])
    assert done.status_code == 200
    assert str(notice.public_id) in done.content.decode()


def test_the_thanks_page_needs_a_submission_first(client: Client) -> None:
    assert client.get(reverse("dsa_notice_received")).status_code == 404


def test_a_child_abuse_report_may_be_anonymous_and_sends_no_email(client: Client) -> None:
    data = form_data(category="child_abuse", notifier_name="", notifier_email="")
    assert client.post(url(), data, REMOTE_ADDR=IP).status_code == 302
    assert Notice.objects.get().notifier_email == ""
    assert not OutboxEmail.objects.exists()


def test_other_reports_need_a_name_and_an_email(client: Client) -> None:
    response = client.post(url(), form_data(notifier_email="", notifier_name=""), REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert not Notice.objects.exists()


def test_the_good_faith_statement_is_required(client: Client) -> None:
    data = form_data()
    del data["good_faith"]
    assert client.post(url(), data, REMOTE_ADDR=IP).status_code == 200
    assert not Notice.objects.exists()


def test_a_bad_location_is_refused(client: Client) -> None:
    response = client.post(url(), form_data(url="javascript:alert(1)"), REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert not Notice.objects.exists()


def test_the_captcha_is_checked_server_side(client: Client, settings: object) -> None:
    settings.CAPTCHA_ENABLED = True  # type: ignore[attr-defined]
    settings.CAPTCHA_VERIFIER = "apps.accounts.tests.fakes.FakeCaptchaVerifier"  # type: ignore[attr-defined]
    assert client.post(url(), form_data(), REMOTE_ADDR=IP).status_code == 200
    assert not Notice.objects.exists()
    token = {TOKEN_FIELD: FakeCaptchaVerifier.VALID_TOKEN}
    assert client.post(url(), form_data(**token), REMOTE_ADDR=IP).status_code == 302
    assert Notice.objects.count() == 1


def test_submissions_are_rate_limited_per_ip(client: Client) -> None:
    for _ in range(services.NOTICE_RATE_LIMIT):
        client.post(url(), form_data(), REMOTE_ADDR=IP)
    response = client.post(url(), form_data(), REMOTE_ADDR=IP)
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 0
    assert Notice.objects.count() == services.NOTICE_RATE_LIMIT
    assert client.get(url(), REMOTE_ADDR=IP).status_code == 200
    assert client.post(url(), form_data(), REMOTE_ADDR="198.51.100.4").status_code == 302


def test_the_receipt_audit_entry_holds_no_personal_data(client: Client) -> None:
    client.post(url(), form_data(), REMOTE_ADDR=IP)
    entry = AuditLog.objects.get(action="compliance.notice.received")
    text = str(entry.before) + str(entry.after)
    for personal in ("ada@example.org", "Ada Reporter", "counterfeit"):
        assert personal not in text
    assert entry.actor_label == "anonymous"


# --- Workflow


def test_review_then_decision_are_audited_and_the_notifier_is_told() -> None:
    notice = NoticeFactory.create()
    services.start_review(notice.public_id, ctx=CTX)
    notice.refresh_from_db()
    assert notice.status == Notice.Status.UNDER_REVIEW
    services.decide_notice(
        notice.public_id, outcome="action_taken", reason="Removed from the catalogue.", ctx=CTX
    )
    notice.refresh_from_db()
    assert notice.status == Notice.Status.ACTION_TAKEN
    assert notice.decision_reason == "Removed from the catalogue."
    assert notice.decided_at is not None
    actions = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
    assert actions == ["compliance.notice.review_started", "compliance.notice.decided"]
    decision_mail = OutboxEmail.objects.get(template_key="compliance.notice_decision")
    assert decision_mail.recipient == notice.notifier_email


def test_a_dry_run_changes_nothing() -> None:
    notice = NoticeFactory.create()
    changes = services.start_review(notice.public_id, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    notice.refresh_from_db()
    assert notice.status == Notice.Status.RECEIVED
    assert not AuditLog.objects.exists()


def test_out_of_order_transitions_are_refused() -> None:
    notice = NoticeFactory.create()
    with pytest.raises(services.NoticeError):
        services.decide_notice(notice.public_id, outcome="no_action", reason="x", ctx=CTX)
    decided = NoticeFactory.create(status=Notice.Status.NO_ACTION)
    with pytest.raises(services.NoticeError):
        services.start_review(decided.public_id, ctx=CTX)


def test_a_decision_needs_a_reason_and_a_known_outcome() -> None:
    notice = NoticeFactory.create(status=Notice.Status.UNDER_REVIEW)
    with pytest.raises(services.NoticeError):
        services.decide_notice(notice.public_id, outcome="no_action", reason=" ", ctx=CTX)
    with pytest.raises(services.NoticeError):
        services.decide_notice(notice.public_id, outcome="received", reason="x", ctx=CTX)


def test_an_anonymous_notifier_gets_no_decision_email() -> None:
    notice = NoticeFactory.create(
        status=Notice.Status.UNDER_REVIEW, category="child_abuse", notifier_email=""
    )
    services.decide_notice(notice.public_id, outcome="no_action", reason="Not found.", ctx=CTX)
    assert not OutboxEmail.objects.exists()


def test_the_decision_email_goes_out_in_the_notifier_language() -> None:
    notice = NoticeFactory.create(status=Notice.Status.UNDER_REVIEW, notifier_language="it")
    services.decide_notice(notice.public_id, outcome="no_action", reason="Fine.", ctx=CTX)
    assert OutboxEmail.objects.get().language == "it"


# --- Manager (platform space)


@pytest.fixture
def reviewer() -> object:
    return UserFactory.create(permissions=["core.platform_owner", "compliance.review_notices"])


def login(client: Client, user: object) -> None:
    verified_login(client, user)  # type: ignore[arg-type]


def test_reviewers_list_notices_and_filter_by_status(client: Client, reviewer: object) -> None:
    open_one = NoticeFactory.create(explanation="First report")
    NoticeFactory.create(status=Notice.Status.NO_ACTION, explanation="Second report")
    login(client, reviewer)
    page = client.get("/manager/platform/notices/")
    assert page.status_code == 200
    assert str(open_one.public_id)[:8] in page.content.decode()
    filtered = client.get("/manager/platform/notices/", {"status": "no_action"})
    assert str(open_one.public_id)[:8] not in filtered.content.decode()


def test_a_reviewer_moves_a_notice_through_the_workflow(client: Client, reviewer: object) -> None:
    notice = NoticeFactory.create()
    login(client, reviewer)
    detail = f"/manager/platform/notices/{notice.public_id}/"
    page = client.get(detail)
    assert page.status_code == 200
    assert notice.notifier_email in page.content.decode()
    assert client.post(detail, {"action": "review"}).status_code == 302
    notice.refresh_from_db()
    assert notice.status == Notice.Status.UNDER_REVIEW
    response = client.post(
        detail, {"action": "decide", "outcome": "no_action", "reason": "Lawful content."}
    )
    assert response.status_code == 302
    notice.refresh_from_db()
    assert (notice.status, notice.decision_reason) == (Notice.Status.NO_ACTION, "Lawful content.")
    assert AuditLog.objects.filter(action="compliance.notice.decided").exists()


def test_a_decision_without_a_reason_shows_an_error_and_changes_nothing(
    client: Client, reviewer: object
) -> None:
    notice = NoticeFactory.create(status=Notice.Status.UNDER_REVIEW)
    login(client, reviewer)
    detail = f"/manager/platform/notices/{notice.public_id}/"
    response = client.post(detail, {"action": "decide", "outcome": "no_action", "reason": ""})
    assert response.status_code == 200
    notice.refresh_from_db()
    assert notice.status == Notice.Status.UNDER_REVIEW


def test_platform_staff_without_the_notice_permission_get_404(client: Client) -> None:
    notice = NoticeFactory.create()
    login(client, UserFactory.create(permissions=["core.platform_owner"]))
    assert client.get("/manager/platform/notices/").status_code == 404
    assert client.get(f"/manager/platform/notices/{notice.public_id}/").status_code == 404


def test_a_superuser_alone_does_not_hold_the_permission(client: Client) -> None:
    login(client, UserFactory.create(is_superuser=True, is_staff=True))
    assert client.get("/manager/platform/notices/").status_code in (302, 403, 404)


def test_visitors_are_sent_to_sign_in(client: Client) -> None:
    assert client.get("/manager/platform/notices/").status_code in (302, 403, 404)


def test_an_unknown_notice_is_404(client: Client, reviewer: object) -> None:
    login(client, reviewer)
    assert client.get(f"/manager/platform/notices/{uuid.uuid4()}/").status_code == 404


# --- Security review fixes


def test_a_child_abuse_report_stores_no_ip_in_the_audit_log(client: Client) -> None:
    data = form_data(category="child_abuse", notifier_name="", notifier_email="")
    client.post(url(), data, REMOTE_ADDR=IP)
    assert AuditLog.objects.get(action="compliance.notice.received").ip is None
    assert not Throttle.objects.filter(key__contains=IP).exists()  # hashed in the counter


def test_other_reports_keep_the_ip_in_the_audit_log(client: Client) -> None:
    client.post(url(), form_data(), REMOTE_ADDR=IP)
    assert AuditLog.objects.get(action="compliance.notice.received").ip == IP


@pytest.mark.parametrize(
    "bad", ["https://x.example/a b", "https://x.example/a\nBcc: x", "https://"]
)
def test_the_location_has_no_whitespace_or_control_characters(bad: str) -> None:
    assert "url" in [
        p.code
        for p in engine.notice_problems(
            explanation="x",
            url=bad,
            category="general",
            notifier_name="n",
            notifier_email="a@example.org",
            good_faith=True,
        )
    ]


def test_receipts_to_one_address_are_limited_per_day(client: Client) -> None:
    for n in range(services.NOTICE_RECEIPTS_PER_ADDRESS + 2):
        client.post(url(), form_data(), REMOTE_ADDR=f"198.51.100.{n + 10}")
    assert Notice.objects.count() == services.NOTICE_RECEIPTS_PER_ADDRESS + 2
    assert OutboxEmail.objects.count() == services.NOTICE_RECEIPTS_PER_ADDRESS
    assert not Throttle.objects.filter(key__contains="ada@example.org").exists()


def test_an_address_django_rejects_gives_a_form_error_not_a_500(client: Client) -> None:
    response = client.post(url(), form_data(notifier_email="a@-x.com"), REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert not Notice.objects.exists()


def test_a_wrong_captcha_does_not_use_up_the_report_limit(client: Client, settings: object) -> None:
    settings.CAPTCHA_ENABLED = True  # type: ignore[attr-defined]
    settings.CAPTCHA_VERIFIER = "apps.accounts.tests.fakes.FakeCaptchaVerifier"  # type: ignore[attr-defined]
    for _ in range(services.NOTICE_RATE_LIMIT):
        client.post(url(), form_data(), REMOTE_ADDR=IP)
    token = {TOKEN_FIELD: FakeCaptchaVerifier.VALID_TOKEN}
    assert client.post(url(), form_data(**token), REMOTE_ADDR=IP).status_code == 302


def test_the_audit_snapshot_keeps_only_scheme_host_and_path() -> None:
    notice = NoticeFactory.build(url="https://shop.example.com/p/x/?email=a@b.c#frag")
    assert services.notice_snapshot(notice)["url"] == "https://shop.example.com/p/x/"


def test_staff_viewing_the_notifier_details_is_audited(client: Client, reviewer: object) -> None:
    notice = NoticeFactory.create()
    login(client, reviewer)
    client.get(f"/manager/platform/notices/{notice.public_id}/")
    entry = AuditLog.objects.get(action="compliance.notice.viewed")
    assert entry.target_key == str(notice.public_id)
    assert (entry.after or {})["reason"]
    assert notice.notifier_email not in str(entry.after)
