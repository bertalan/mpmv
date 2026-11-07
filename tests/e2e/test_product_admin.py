"""E2E: staff create a product with a translation, then change its price (CAT.1)."""

import secrets
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.models import Product
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.login import admin_login, verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def test_staff_create_a_product_and_change_its_price(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)  # staff must verify a second factor (CORE.4)
    vendor = VendorFactory.create(display_name="Vendor A")
    admin_login(page, live_server.url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/django-admin/catalog/product/add/")
    page.locator("#id_vendor").fill(str(vendor.pk))
    page.locator("#id_sku").fill("bag-01")
    page.locator("#id_price_net").fill("25.00")
    page.locator("#id_weight_kg").fill("0.750")
    page.locator("#id_translations-0-language").select_option("en")
    page.locator("#id_translations-0-title").fill("Leather bag")
    shot("filled")
    page.get_by_role("button", name="Save and continue editing").click()
    expect(page.locator(".messagelist")).to_contain_text("was added successfully")

    product = Product.objects.get()
    assert product.sku == "BAG-01"
    assert product.translations.get(language="en").title == "Leather bag"

    page.locator("#id_price_net").fill("29.00")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".messagelist")).to_contain_text("was changed successfully")
    shot("price-changed")

    product.refresh_from_db()
    assert product.price_net == Decimal("29.00")
    entry = AuditLog.objects.get(action="catalog.product.price_changed")
    assert (entry.before or {}).get("price_net") == "25.00"
    assert (entry.after or {}).get("price_net") == "29.00"
