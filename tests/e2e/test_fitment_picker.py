"""E2E (FIT.3 follow-up 2/2): a vendor picks Make, then Model, in the cascading picker of the Fits
page, previews and confirms; the saved row is the chosen node. The native select stays the field."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.tests.factories import ProductFactory
from apps.fitment.models import ProductFitment
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    FitmentSchemeFactory,
)
from apps.manager.role_groups import sync_role_groups
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_vendor_picks_make_then_model_and_saves(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    sync_role_groups(Group, Permission, ContentType, apps.get_model)
    FitmentSchemeFactory.create()
    FitmentLevelFactory.create(position=1, code="make")
    FitmentLevelFactory.create(position=2, code="model")
    make = FitmentNodeFactory.create(name="Make One")
    model = FitmentNodeFactory.create(name="Model A", parent=make)
    FitmentNodeFactory.create(name="Model Proposed", parent=make, status="proposed")
    shop = VendorFactory.create(display_name="Shop West", status="active")
    product = ProductFactory.create(vendor=shop)
    password = secrets.token_urlsafe(16)
    owner = UserFactory.create(email="owner@example.com", password=password)
    device = TOTPDeviceFactory.create(user=owner)
    VendorMemberFactory.create(vendor=shop, user=owner, role="owner")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/login/")
    page.get_by_label("Email").fill("owner@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/fitment/{product.public_id}/")

    first_row = page.locator("tbody tr").first
    picker = first_row.locator(".fitment-picker")
    expect(picker.locator("select")).to_have_count(1)
    expect(first_row.locator("select[data-fitment-picker]")).to_be_hidden()
    expect(picker.get_by_role("combobox").first.locator("option")).to_have_count(2)
    picker.get_by_role("combobox").first.select_option(label="Make One")
    expect(picker.locator("select")).to_have_count(2)
    expect(picker.locator("select").nth(1).locator("option")).to_have_count(2)  # not the proposed
    expect(page.locator("#fitment-picker-status")).to_contain_text("1")
    picker.locator("select").nth(1).select_option(label="Model A")
    page.get_by_label("Specific items").check()
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    shot("fitment-picker")
    page.get_by_role("button", name="Preview", exact=True).click()
    page.get_by_role("button", name="Confirm", exact=True).click()
    expect(page.get_by_text("The fitment of this product is saved.")).to_be_visible()
    assert list(ProductFitment.objects.values_list("node_id", flat=True)) == [model.pk]
    assert csp_violations(page) == []
