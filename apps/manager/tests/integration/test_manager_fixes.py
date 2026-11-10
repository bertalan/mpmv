"""CORE.17 fixes: CSRF on every Manager form, the Manager root, the explanatory 403."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login

from .conftest import LOGIN, PLATFORM, SHOP

pytestmark = pytest.mark.django_db

FORM = re.compile(r"<form\b.*?</form>", re.S)
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def _forms(html: str) -> list[str]:
    return [form for form in FORM.findall(html) if 'method="post"' in form]


def test_every_form_in_the_manager_shell_carries_a_csrf_token(
    client: Client, platform_staff: User
) -> None:
    """The layout includes the language switcher with `only`; it must still get the token."""
    verified_login(client, platform_staff)
    forms = _forms(client.get(PLATFORM).text)
    assert len(forms) >= 2  # sign out and one per language
    for form in forms:
        assert TOKEN.search(form), form[:200]


def test_the_language_switcher_posts_through_a_real_csrf_check(
    client: Client, platform_staff: User
) -> None:
    strict = Client(enforce_csrf_checks=True)
    verified_login(strict, platform_staff)
    html = strict.get(PLATFORM).text
    form = next(f for f in _forms(html) if 'name="language"' in f)
    token = TOKEN.search(form)
    assert token
    action = re.search(r'action="([^"]+)"', form)
    assert action
    code = re.search(r'name="language" value="([^"]+)"', form)
    assert code
    nxt = re.search(r'name="next" value="([^"]*)"', form)
    assert nxt
    data = {"csrfmiddlewaretoken": token.group(1), "language": code.group(1), "next": nxt.group(1)}
    assert strict.post(action.group(1), data).status_code in (302, 200)
    assert (
        strict.post(
            action.group(1), {k: v for k, v in data.items() if k != "csrfmiddlewaretoken"}
        ).status_code
        == 403
    )


def test_sign_out_posts_through_a_real_csrf_check(client: Client, platform_staff: User) -> None:
    strict = Client(enforce_csrf_checks=True)
    verified_login(strict, platform_staff)
    html = strict.get(PLATFORM).text
    form = next(f for f in _forms(html) if "/account/logout/" in f)
    token = TOKEN.search(form)
    assert token
    assert (
        strict.post("/account/logout/", {"csrfmiddlewaretoken": token.group(1)}).status_code == 302
    )
    assert strict.post("/account/logout/").status_code == 403


def test_the_shop_space_forms_carry_a_csrf_token(client: Client, owner: User) -> None:
    verified_login(client, owner)
    for form in _forms(client.get(SHOP).text):
        assert TOKEN.search(form), form[:200]


# --- /manager/ root


def test_the_root_sends_platform_staff_to_the_platform_home(
    client: Client, platform_staff: User
) -> None:
    verified_login(client, platform_staff)
    response = client.get("/manager/")
    assert response.status_code == 302
    assert response["Location"] == PLATFORM


def test_the_root_sends_a_vendor_member_to_the_shop_home(client: Client, owner: User) -> None:
    verified_login(client, owner)
    response = client.get("/manager/")
    assert response.status_code == 302
    assert response["Location"] == SHOP


def test_the_root_sends_an_anonymous_visitor_to_the_login(client: Client) -> None:
    response = client.get("/manager/")
    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)


def test_the_root_gives_a_user_without_a_space_the_explanatory_403(
    client: Client, customer: User
) -> None:
    client.force_login(customer)
    assert client.get("/manager/").status_code == 403


# --- explanatory 403


@pytest.mark.parametrize("path", [PLATFORM, SHOP])
def test_a_signed_in_user_without_the_right_gets_an_explanatory_403(
    client: Client, customer: User, path: str
) -> None:
    client.force_login(customer)
    response = client.get(path)
    assert response.status_code == 403
    assert "do not have access" in response.text
    assert customer.email in response.text


def test_the_403_page_offers_sign_out_with_a_csrf_token(client: Client, customer: User) -> None:
    client.force_login(customer)
    forms = _forms(client.get(PLATFORM).text)
    assert any("/account/logout/" in f and TOKEN.search(f) for f in forms)


def test_a_vendor_member_gets_the_403_on_the_platform_post_too(client: Client, owner: User) -> None:
    verified_login(client, owner)
    assert client.post(PLATFORM).status_code == 403


def test_an_anonymous_visitor_still_goes_to_the_login_not_the_403(client: Client) -> None:
    response = client.get(PLATFORM)
    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)


def test_a_path_outside_both_spaces_is_still_404_for_a_signed_in_user(
    client: Client, platform_staff: User
) -> None:
    verified_login(client, platform_staff)
    assert client.get("/manager/nowhere/").status_code == 404


def test_an_editor_with_only_the_staff_flag_gets_the_403(client: Client) -> None:
    verified_login(client, UserFactory.create(staff=True))
    assert client.get(PLATFORM).status_code == 403


@pytest.mark.parametrize("path", ["/manager", "//manager/", "/MANAGER/", "/manager//platform/"])
def test_odd_spellings_of_the_root_never_reach_a_space(
    client: Client, platform_staff: User, path: str
) -> None:
    verified_login(client, platform_staff)
    response = client.get(path)
    assert response.status_code in (200, 301, 302, 404)
    if response.status_code == 200:  # the CMS catch-all, never a Manager page
        assert "manager-header" not in response.text
    elif response.status_code != 404:
        assert response["Location"] != PLATFORM


def test_the_anonymous_root_redirect_carries_its_own_path_as_next(client: Client) -> None:
    response = client.get("/manager/?next=https://evil.example/&x=1")
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.path == LOGIN
    assert parse_qs(location.query)["next"] == ["/manager/?next=https://evil.example/&x=1"]
