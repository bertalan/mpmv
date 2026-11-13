"""OPS.14: the deployed version is shown to staff only, never to visitors or customers."""

import importlib
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.themes.context_processors import release
from mpmv.settings import base

pytestmark = pytest.mark.django_db

VERSION = "7.8.9-ops14"
PLATFORM = "/manager/platform/"


@pytest.fixture(autouse=True)
def _version() -> Iterator[None]:
    with override_settings(MPMV_VERSION=VERSION):
        yield


def test_staff_see_the_version_on_a_manager_page_and_in_the_account_menu(client: Client) -> None:
    verified_login(client, UserFactory.create(staff=True, permissions=["core.platform_owner"]))
    manager = client.get(PLATFORM)
    assert manager.status_code == 200
    assert f"Version {VERSION}" in manager.text
    assert 'class="manager-footer__version"' in manager.text
    account = client.get(reverse("mfa:overview"))
    assert account.status_code == 200
    assert f'<span class="user-menu__version">Version {VERSION}</span>' in account.text


def test_a_customer_never_sees_the_version(client: Client) -> None:
    verified_login(client, UserFactory.create())
    page = client.get(reverse("mfa:overview"))
    assert page.status_code == 200
    assert VERSION not in page.text
    assert "user-menu__version" not in page.text


def test_an_anonymous_visitor_never_sees_the_version(client: Client) -> None:
    for path in ("/", reverse("login"), PLATFORM):
        page = client.get(path, follow=True)
        assert VERSION not in page.text, path


def test_the_value_is_dev_when_the_variable_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPMV_VERSION", raising=False)
    assert importlib.reload(base).MPMV_VERSION == "dev"
    monkeypatch.setenv("MPMV_VERSION", "0.1.11")
    assert importlib.reload(base).MPMV_VERSION == "0.1.11"


def test_the_context_processor_is_empty_without_staff_status() -> None:
    request = RequestFactory().get("/")
    request.user = UserFactory.create()
    assert release(request) == {"release_version": ""}
    request.user = UserFactory.create(staff=True)
    assert release(request) == {"release_version": settings.MPMV_VERSION}
