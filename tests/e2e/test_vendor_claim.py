"""E2E (VEN.24): a claimant opens the emailed claim link, presses Continue, confirms the VAT
number, sets a password and enrols an authenticator app; the claim then waits for staff."""

import json
import re
import secrets
import time
from base64 import b32decode
from collections.abc import Callable
from pathlib import Path

import pytest
from django_otp.oath import TOTP
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.models import ActionLinkUse, User
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors import services
from apps.vendors.models import Vendor, VendorClaim
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db(transaction=True)

SYSTEM = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def emailed_link_path() -> str:
    email = OutboxEmail.objects.order_by("-id").first()
    assert email is not None
    return "/" + json.loads(email.context)["action_url"].split("/", 3)[3]


def code_from_secret(secret: str) -> str:
    key = b32decode(secret.replace(" ", "").upper())
    totp = TOTP(key, 30, 0, 6, 0)
    totp.time = time.time()
    return str(totp.token()).zfill(6)


def test_claim_an_imported_vendor(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    vendor = VendorFactory.create(italian_company=True, contact_email="legal@vendor-a.example")
    services.send_claim_link(vendor, ctx=SYSTEM)

    page.goto(f"{live_server.url}{emailed_link_path()}")
    expect(page.get_by_role("button", name="Continue")).to_be_visible()
    shot("claim-continue")
    assert not ActionLinkUse.objects.exists()  # the GET spent nothing

    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", name=re.compile("Claim"))).to_be_visible()
    page.get_by_label("VAT number or TIN").fill("IT00000000000")
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_text("does not match")).to_be_visible()
    shot("claim-wrong-vat")

    page.get_by_label("VAT number or TIN").fill(vendor.vat_number)
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_label("Password", exact=True)).to_be_visible()
    shot("claim-account")

    password = secrets.token_urlsafe(16)
    secret = page.get_by_test_id("totp-secret").inner_text()
    page.get_by_label("Full name").fill("Claimant Example")
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_label("Password again").fill(password)
    page.get_by_label("Authentication code").fill(code_from_secret(secret))
    page.get_by_role("button", name="Submit the claim").click()
    expect(page.get_by_text("waiting for approval")).to_be_visible()
    shot("claim-submitted")

    claim = VendorClaim.objects.get(vendor=vendor)
    assert claim.status == VendorClaim.Status.SUBMITTED
    claimant = User.objects.get(email="legal@vendor-a.example")
    assert claim.claimant == claimant
    assert not Vendor.objects.get(pk=vendor.pk).members.exists()  # staff approve first
    assert AuditLog.objects.filter(action=services.CLAIM_IDENTITY_REFUSED).count() == 1
