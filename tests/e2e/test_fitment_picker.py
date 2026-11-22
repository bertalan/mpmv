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
from apps.fitment.models import FitmentNode, ProductFitment
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
from tests.i18n import translated

pytestmark = pytest.mark.django_db(transaction=True)


def _open_fits_page(page: Page, live_server: LiveServer) -> tuple[FitmentNode, FitmentNode]:
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
    page.get_by_label(translated("Email")).fill("owner@example.com")
    page.get_by_label(translated("Password")).fill(password)
    page.get_by_role("button", name=translated("Sign in")).click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/fitment/{product.public_id}/")
    return make, model


def test_the_vendor_picks_make_then_model_and_saves(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    make, model = _open_fits_page(page, live_server)
    del make

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
    page.get_by_label(translated("Specific items")).check()
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    shot("fitment-picker")
    page.get_by_role("button", name=translated("Preview"), exact=True).click()
    page.get_by_role("button", name=translated("Confirm"), exact=True).click()
    expect(page.get_by_text(translated("The fitment of this product is saved."))).to_be_visible()
    assert list(ProductFitment.objects.values_list("node_id", flat=True)) == [model.pk]
    assert csp_violations(page) == []


def test_the_vendor_finds_a_variant_by_typing_and_saves(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    _make, model = _open_fits_page(page, live_server)
    variant = FitmentNodeFactory.create(name="Variant Sport", parent=model)
    page.reload()
    search = page.locator("tbody tr").first.get_by_role(
        "combobox", name=translated("Search by name")
    )
    search.fill("sport")
    options = page.locator(".fitment-picker__option")
    expect(options).to_have_count(1)
    expect(options.first).to_have_text("Make One > Model A > Variant Sport")
    expect(page.locator("#fitment-picker-status")).to_contain_text("1")
    search.press("ArrowDown")
    shot("fitment-picker-search")
    search.press("Enter")
    expect(options.first).to_be_hidden()
    page.get_by_label(translated("Specific items")).check()
    violations = run_axe(page)
    assert not violations, format_violations(violations)
    page.get_by_role("button", name=translated("Preview"), exact=True).click()
    page.get_by_role("button", name=translated("Confirm"), exact=True).click()
    expect(page.get_by_text(translated("The fitment of this product is saved."))).to_be_visible()
    assert list(ProductFitment.objects.values_list("node_id", flat=True)) == [variant.pk]
    assert csp_violations(page) == []
