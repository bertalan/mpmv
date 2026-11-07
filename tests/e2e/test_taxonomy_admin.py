"""E2E: staff build the category tree with translated texts in the admin (CAT.2), under the
strict Content-Security-Policy (the slug is filled from the name by the admin's own script), and
place a term under another found by the autocomplete, narrowed to the term's taxonomy."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.catalog.models import Category, CategoryTranslation, TermTranslation
from apps.catalog.tests.factories import TaxonomyFactory, TermFactory, TermTranslationFactory
from tests.e2e.login import admin_login, verify_with_totp
from tests.e2e.test_admin_csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def add_category(page: Page, base_url: str, name: str, *, under: str | None = None) -> None:
    page.goto(f"{base_url}/django-admin/catalog/category/add/")
    if under is not None:
        page.locator("#id_treebeard_position").select_option("first-child")
        page.locator("#id_treebeard_ref_node").select_option(label=under)
    page.locator("#id_translations-0-language").select_option("en")
    page.locator("#id_translations-0-status").select_option("published")
    page.locator("#id_translations-0-name").press_sequentially(name)
    page.locator("#id_translations-0-seo_title").fill(f"{name} made by hand")


def staff_login(page: Page, base_url: str) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="staff@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)  # staff must verify a second factor (CORE.4)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    admin_login(page, base_url, "staff@example.com", password)
    verify_with_totp(page, totp_token(device))


def test_staff_build_the_category_tree_with_translations(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    staff_login(page, live_server.url)

    add_category(page, live_server.url, "Bags")
    expect(page.locator("#id_translations-0-slug")).to_have_value("bags")
    shot("root-filled")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".messagelist")).to_contain_text("was added successfully")

    add_category(page, live_server.url, "Backpacks", under="Bags")
    shot("child-filled")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".messagelist")).to_contain_text("was added successfully")
    expect(page.locator("#result_list")).to_contain_text("— Backpacks")
    shot("tree")
    assert csp_violations(page) == []

    bags = CategoryTranslation.objects.get(slug="bags").category
    backpacks = CategoryTranslation.objects.get(slug="backpacks").category
    assert backpacks.get_parent() == bags
    assert list(Category.objects.all()) == [bags, backpacks]
    assert backpacks.translations.get().seo_title == "Backpacks made by hand"


def test_staff_place_a_term_under_another_found_by_autocomplete(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    taxonomy = TaxonomyFactory.create(code="compatibility", hierarchical=True)
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=maker, name="Maker A", slug="maker-a")
    other = TermFactory.create(taxonomy=TaxonomyFactory.create(code="season"))
    TermTranslationFactory.create(term=other, name="Maker season", slug="maker-season")
    staff_login(page, live_server.url)

    page.goto(f"{live_server.url}/django-admin/catalog/term/add/?taxonomy={taxonomy.pk}")
    page.locator("#id_treebeard_position").select_option("first-child")
    page.locator("#id_treebeard_ref_node + .select2 .select2-selection").click()
    page.locator(".select2-search__field").fill("maker")
    options = page.locator(".select2-results__option")
    expect(options).to_have_count(1)  # the other taxonomy's term is not offered
    expect(options).to_have_text("compatibility: Maker A")
    shot("autocomplete")
    options.click()
    page.locator("#id_translations-0-language").select_option("en")
    page.locator("#id_translations-0-status").select_option("published")
    page.locator("#id_translations-0-name").press_sequentially("Model 1")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".messagelist")).to_contain_text("was added successfully")
    shot("term-placed")
    assert csp_violations(page) == []

    model = TermTranslation.objects.get(slug="model-1").term
    assert model.get_parent() == maker
