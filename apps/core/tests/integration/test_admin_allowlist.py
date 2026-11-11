"""OPS.5: the app-level check on the Django admin path (defence in depth behind Caddy)."""

import re

import pytest
from django.test import Client, override_settings

ALLOWED = ["192.0.2.0/24", "2001:db8::/32"]
PROXY = "10.89.250.2"
PROXY_NET = "10.89.250.0/24"
ADMIN = "/django-admin/"

pytestmark = pytest.mark.django_db


def _get(client: Client, ip: str, path: str = ADMIN, real_ip: str | None = None) -> int:
    headers = {"X-Real-IP": real_ip} if real_ip else {}
    return client.get(path, headers=headers, REMOTE_ADDR=ip).status_code


@override_settings(ADMIN_ALLOWED_NETWORKS=ALLOWED)
def test_from_an_allowed_network_the_admin_works(client: Client) -> None:
    response = client.get(ADMIN, REMOTE_ADDR="192.0.2.50")
    assert response.status_code == 302
    assert "login" in response["Location"]
    assert _get(client, "2001:db8::9", ADMIN + "login/") == 200


@override_settings(ADMIN_ALLOWED_NETWORKS=ALLOWED)
@pytest.mark.parametrize("path", [ADMIN, ADMIN + "login/", ADMIN + "auth/user/", ADMIN + "nope/"])
def test_from_outside_every_admin_path_answers_404(client: Client, path: str) -> None:
    assert _get(client, "203.0.113.9", path) == 404
    assert client.post(path, {}, REMOTE_ADDR="203.0.113.9").status_code == 404


@override_settings(ADMIN_ALLOWED_NETWORKS=ALLOWED)
def test_the_refusal_looks_like_any_unknown_page(client: Client) -> None:
    refused = client.get(ADMIN, REMOTE_ADDR="203.0.113.9")
    unknown = client.get("/no-such-page-here/", REMOTE_ADDR="203.0.113.9")
    assert refused.status_code == unknown.status_code == 404
    assert "Location" not in refused
    token = re.compile(rb'(nonce|value)="[^"]*"')  # a new CSP nonce and CSRF token every time
    assert token.sub(b"", refused.content) == token.sub(b"", unknown.content)


@override_settings(ADMIN_ALLOWED_NETWORKS=ALLOWED)
def test_other_paths_are_not_restricted(client: Client) -> None:
    assert _get(client, "203.0.113.9", "/account/login/") == 200
    assert _get(client, "203.0.113.9", "/admin/login/") == 200  # Wagtail: its own gates


@override_settings(
    ADMIN_ALLOWED_NETWORKS=ALLOWED, TRUSTED_PROXY_HEADER="X-Real-IP", TRUSTED_PROXIES=[PROXY_NET]
)
def test_behind_the_trusted_proxy_the_client_address_decides(client: Client) -> None:
    assert _get(client, PROXY, real_ip="192.0.2.77") == 302
    assert _get(client, PROXY, real_ip="203.0.113.9") == 404
    assert _get(client, PROXY) == 404  # no header: the proxy's own address is not allowed


@override_settings(
    ADMIN_ALLOWED_NETWORKS=ALLOWED, TRUSTED_PROXY_HEADER="X-Real-IP", TRUSTED_PROXIES=[PROXY_NET]
)
def test_a_header_from_an_untrusted_peer_is_ignored(client: Client) -> None:
    assert _get(client, "203.0.113.9", real_ip="192.0.2.77") == 404


@override_settings(ADMIN_ALLOWED_NETWORKS=[])
def test_without_networks_the_admin_is_not_restricted_here(client: Client) -> None:
    assert _get(client, "203.0.113.9") == 302
