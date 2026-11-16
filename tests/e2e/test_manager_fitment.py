"""E2E (FIT.1 part 2): the Manager pages of the fitment scheme, categories and nodes at 360 and
1280 px: they open, pass axe (WCAG 2.2 AA), raise no CSP violation and never scroll sideways."""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.tests.factories import CategoryFactory
from apps.fitment.tests.factories import FitmentLevelFactory, FitmentNodeFactory
from apps.manager import roles
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)

SIZES = (360, 1280)


def test_the_fitment_pages_open_and_are_accessible(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, platform_role="config")
    user.groups.add(Group.objects.get(name=roles.role("catalog").group))
    device = TOTPDeviceFactory.create(user=user)
    FitmentLevelFactory.create(position=1, code="make")
    FitmentLevelFactory.create(position=2, code="model")
    make = FitmentNodeFactory.create(name="Make One")
    FitmentNodeFactory.create(name="Model A", parent=make)
    FitmentNodeFactory.create(name="Make Proposed", status="proposed")
    CategoryFactory.create()
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/login/")
    page.get_by_label("Email").fill("staff@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(re.compile(r"/account/mfa/verify/"))
    verify_with_totp(page, totp_token(device), window=True)

    pages = {
        "scheme": ("/manager/platform/fitment/scheme/", "Fitment scheme"),
        "categories": (
            "/manager/platform/fitment/categories/",
            "Categories expected to declare fitment",
        ),
        "nodes": ("/manager/platform/fitment/nodes/", "Fitment nodes"),
        "proposals": ("/manager/platform/fitment/nodes/proposals/", "Proposed nodes"),
        "new-node": ("/manager/platform/fitment/nodes/new/", "New fitment node"),
    }
    for width in SIZES:
        page.set_viewport_size({"width": width, "height": 900})
        for name, (path, heading) in pages.items():
            page.goto(f"{live_server.url}{path}")
            expect(page.get_by_role("heading", name=heading, level=1)).to_be_visible()
            violations = run_axe(page)
            assert not violations, format_violations(violations)
            assert csp_violations(page) == []
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            assert not overflow, f"{name} scrolls sideways at {width}px"
            shot(f"fitment-{name}-{width}")

    # A change shows its dry-run preview first (the passkey window is open for the staff user).
    page.goto(f"{live_server.url}/manager/platform/fitment/scheme/")
    form = page.locator("form:has(input[name=action][value=add_level])")
    form.get_by_label("Code").fill("year")
    form.get_by_label("Label (English)", exact=True).fill("Year")
    form.get_by_label("Plural label (English)").fill("Years")
    form.get_by_role("button", name="Preview the level").click()
    expect(page.get_by_role("heading", name="Preview of the change", level=1)).to_be_visible()
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    assert csp_violations(page) == []
    shot("fitment-scheme-preview")
