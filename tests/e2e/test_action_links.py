"""E2E (CORE.15): an emailed secure action link opens a "Continue" page; its POST signs the
recipient in without a password, the action page asks for the MFA step-up, then the approval is
a POST."""

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Request, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts import services
from apps.accounts.models import ActionLinkUse
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.integration.urls import APPROVE_PURPOSE, APPROVED, DOCUMENTS, Document
from apps.accounts.tests.mfa import totp_token
from apps.core.models import AuditLog
from tests.e2e.login import verify_with_totp

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.urls("apps.accounts.tests.integration.urls"),
]


@pytest.fixture(autouse=True)
def _documents() -> Iterator[None]:
    DOCUMENTS.clear()
    APPROVED.clear()
    yield
    DOCUMENTS.clear()
    APPROVED.clear()


def test_link_get_then_continue_post_then_step_up_then_approve(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    user = UserFactory.create(email="vendor@example.com")
    device = TOTPDeviceFactory.create(user=user)
    DOCUMENTS["doc-1"] = Document(owner_id=user.pk, version="1")
    token = services.issue_action_token(
        purpose=APPROVE_PURPOSE, object_key="doc-1", recipient=user, version="1"
    )
    sent_referers: list[str] = []

    def remember_referer(request: Request) -> None:
        sent_referers.append(request.headers.get("referer", ""))

    page.on("request", remember_referer)

    page.goto(f"{live_server.url}{services.action_link_path(token)}")
    expect(page.get_by_role("button", name="Continue")).to_be_visible()
    shot("continue-page")
    assert not ActionLinkUse.objects.exists()  # the GET spent nothing

    page.get_by_role("button", name="Continue").click()
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    shot("step-up")
    assert ActionLinkUse.objects.count() == 1

    verify_with_totp(page, totp_token(device))
    expect(page.get_by_role("heading", name="Approve document doc-1")).to_be_visible()
    shot("action-page")

    page.get_by_role("button", name="Approve").click()
    expect(page.get_by_text("approved doc-1")).to_be_visible()
    shot("approved")
    assert [("doc-1", user.pk)] == APPROVED
    assert AuditLog.objects.filter(action=services.ACTION_LINK_SIGNED_IN).count() == 1
    assert not any(token in referer for referer in sent_referers)


def test_a_used_link_sends_to_the_normal_login(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    user = UserFactory.create()
    TOTPDeviceFactory.create(user=user)
    DOCUMENTS["doc-1"] = Document(owner_id=user.pk, version="1")
    token = services.issue_action_token(
        purpose=APPROVE_PURPOSE, object_key="doc-1", recipient=user, version="1"
    )
    link = f"{live_server.url}{services.action_link_path(token)}"

    page.goto(link)
    page.get_by_role("button", name="Continue").click()
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))

    page.context.clear_cookies()
    page.goto(link)
    page.get_by_role("button", name="Continue").click()
    expect(page).to_have_url(re.compile(r"/django-admin/login/\?next="))
    shot("used-link-login")
    assert AuditLog.objects.filter(action=services.ACTION_LINK_REFUSED).count() == 1
