"""CORE.4: where an unverified, enrolled user lands after a refused POST.

`MfaRequiredMiddleware` must not send a POST endpoint as `next` (the GET after verifying would
answer 405). For POST it uses the same-origin Referer, if it is safe and not an auth/MFA page,
else the account home. GET keeps the current request path.
"""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.http.response import HttpResponseBase
from django.test import Client
from django.urls import resolve, reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login, totp_token

pytestmark = pytest.mark.django_db

POST_ONLY = "/django-admin/accounts/user/add/"
HOME = "/"


def _enrolled_staff(client: Client) -> tuple[User, TOTPDevice]:
    user = UserFactory.create(superuser=True)
    device = TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    return user, device


def _next_of(response: HttpResponseBase) -> str:
    assert response.status_code == 302
    parts = urlsplit(response["Location"])
    assert parts.path == reverse("mfa:verify")
    return parse_qs(parts.query).get("next", [""])[0]


def _post(client: Client, referer: str | None) -> HttpResponseBase:
    headers = {"Referer": referer} if referer is not None else {}
    return client.post(POST_ONLY, {}, headers=headers)


def test_post_uses_a_same_origin_referer_as_next(client: Client) -> None:
    _enrolled_staff(client)
    response = _post(client, "http://testserver/django-admin/accounts/user/?q=1")
    assert _next_of(response) == "/django-admin/accounts/user/?q=1"


@pytest.mark.parametrize(
    "referer",
    [
        "http://evil.example/django-admin/",
        "https://evil.example/",
        "//evil.example/",
        "https://testserver.evil.example/",
        "javascript:alert(1)",
        "http://testserver//evil.example/",
        "http://testserver/%2F%2Fevil.example/",
        "http://testserver/a\\b/",
        "http://testserver/a%5Cb/",
        "https://good@evil.example/",
        "http://testserver@evil.example/",
        "http://testserver:8000/django-admin/",
        "http://testserver/django-admin/\t",
        "http://testserver/django-admin/\n",
        "",
    ],
)
def test_post_refuses_foreign_or_malformed_referers(client: Client, referer: str) -> None:
    _enrolled_staff(client)
    assert _next_of(_post(client, referer)) == HOME


def test_post_without_a_referer_falls_back_to_home(client: Client) -> None:
    _enrolled_staff(client)
    assert _next_of(_post(client, None)) == HOME


DENIED = [
    ("mfa:verify", {}),
    ("mfa:setup", {}),
    ("mfa:totp", {}),
    ("login", {}),
    ("logout", {}),
    ("wagtailadmin_login", {}),
    ("wagtailadmin_logout", {}),
    ("admin:login", {}),
    ("admin:logout", {}),
    ("action_link:open", {"token": "some-token"}),
]


def _denied_paths() -> list[str]:
    return [reverse(name, kwargs=kwargs) for name, kwargs in DENIED]


@pytest.mark.parametrize(("name", "kwargs"), DENIED)
def test_post_never_returns_to_an_auth_or_mfa_page(
    client: Client, name: str, kwargs: dict[str, str]
) -> None:
    _enrolled_staff(client)
    path = reverse(name, kwargs=kwargs)
    assert resolve(path).route  # a real route: the test cannot pass on a silent 404
    assert _next_of(_post(client, f"http://testserver{path}?next=/")) == HOME


@pytest.mark.parametrize("index", range(len(DENIED)))
def test_post_refuses_a_percent_encoded_auth_path(client: Client, index: int) -> None:
    _enrolled_staff(client)
    path = _denied_paths()[index]
    encoded = path[:-1].replace(path[-2], f"%{ord(path[-2]):02X}", 1) + "/"
    assert encoded != path
    assert _next_of(_post(client, f"http://testserver{encoded}")) == HOME


def test_get_keeps_the_current_path_and_ignores_the_referer(client: Client) -> None:
    _enrolled_staff(client)
    response = client.get(
        "/django-admin/accounts/user/?q=2", headers={"Referer": "http://testserver/other/"}
    )
    assert _next_of(response) == "/django-admin/accounts/user/?q=2"


def test_head_keeps_the_current_path(client: Client) -> None:
    _enrolled_staff(client)
    assert _next_of(client.head("/django-admin/")) == "/django-admin/"


def test_json_post_still_answers_403(client: Client) -> None:
    _enrolled_staff(client)
    response = client.post(
        POST_ONLY, {}, headers={"Accept": "application/json", "Referer": "http://testserver/"}
    )
    assert response.status_code == 403
    assert response.json() == {"error": "mfa_required", "url": reverse("mfa:verify")}


def test_after_verifying_the_user_lands_on_a_page_that_answers_200(client: Client) -> None:
    _, device = _enrolled_staff(client)
    response = _post(client, "http://testserver/django-admin/")
    verify_url = response["Location"]
    verified = client.post(
        verify_url,
        {"method": "totp", "token": totp_token(device)},
    )
    assert verified.status_code == 302
    assert verified["Location"] == "/django-admin/"
    assert client.get(verified["Location"]).status_code == 200


def test_after_verifying_without_a_referer_the_user_lands_on_home(client: Client) -> None:
    _, device = _enrolled_staff(client)
    verify_url = _post(client, None)["Location"]
    verified = client.post(verify_url, {"method": "totp", "token": totp_token(device)})
    assert verified["Location"] == HOME
    assert client.get(HOME).status_code == 200


def test_secure_requests_refuse_a_plain_http_referer(client: Client) -> None:
    _enrolled_staff(client)
    response = client.post(
        POST_ONLY,
        {},
        secure=True,
        headers={"Referer": "http://testserver/django-admin/"},
    )
    assert _next_of(response) == HOME
    ok = client.post(
        POST_ONLY, {}, secure=True, headers={"Referer": "https://testserver/django-admin/"}
    )
    assert _next_of(ok) == "/django-admin/"
