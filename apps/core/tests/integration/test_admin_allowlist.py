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
@pytest.mark.parametrize("language", ["", "it", "en"])
@pytest.mark.parametrize("path", [ADMIN, ADMIN.rstrip("/"), ADMIN + "login/"])
def test_the_refusal_is_the_normal_404(client: Client, path: str, language: str) -> None:
    """Same status, body (language and theme), header names and values as an unknown URL."""
    headers = {"Accept-Language": language} if language else {}
    with override_settings(FORCE_NOINDEX=True):
        refused = client.get(path, headers=headers, REMOTE_ADDR="203.0.113.9")
        unknown = client.get("/no-such-page-here/", headers=headers, REMOTE_ADDR="203.0.113.9")
    assert refused.status_code == unknown.status_code == 404
    assert "Location" not in refused
    token = re.compile(rb'(nonce|value)="[^"]*"')  # a new CSP nonce and CSRF token every time
    assert token.sub(b"", refused.content) == token.sub(b"", unknown.content)
    for name in ("X-Robots-Tag", "Permissions-Policy", "X-Frame-Options", "Content-Language"):
        assert refused.get(name) == unknown.get(name)
    # a per-response nonce; CommonMiddleware adds the length (the server does for the refusal)
    ignore = {"Content-Security-Policy", "Content-Length"}
    assert {k for k in refused.headers if k not in ignore} == {
        k for k in unknown.headers if k not in ignore
    }


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
@pytest.mark.parametrize("ip", ["127.0.0.1", "203.0.113.9"])
def test_without_networks_nobody_gets_in(client: Client, ip: str) -> None:
    """Fail closed: an empty list (the variable was missed) denies everybody."""
    assert _get(client, ip) == 404
    assert _get(client, ip, ADMIN + "login/") == 404


def test_the_test_settings_allow_loopback_only(client: Client) -> None:
    from django.conf import settings

    assert settings.ADMIN_ALLOWED_NETWORKS == ["127.0.0.1/32", "::1/128"]
    assert _get(client, "127.0.0.1") == 302
    assert _get(client, "203.0.113.9") == 404


LOOPBACK = ["127.0.0.1/32", "::1/128"]


@override_settings(
    ADMIN_ALLOWED_NETWORKS=LOOPBACK, TRUSTED_PROXY_HEADER="X-Real-IP", TRUSTED_PROXIES=[PROXY_NET]
)
def test_the_ssh_tunnel_path_reaches_the_admin_and_a_direct_visit_does_not(client: Client) -> None:
    """Loopback-only policy (MQ-24): through the tunnel Caddy sees 127.0.0.1 and forwards it as
    X-Real-IP, the browser's Host carries the local tunnel port; a public address gets 404."""
    tunnel = {"Host": "testserver:8443", "X-Real-IP": "127.0.0.1"}
    response = client.get(ADMIN, headers=tunnel, REMOTE_ADDR=PROXY)
    assert response.status_code == 302
    assert "login" in response["Location"]
    assert client.get(ADMIN + "login/", headers=tunnel, REMOTE_ADDR=PROXY).status_code == 200
    tunnel6 = {**tunnel, "X-Real-IP": "::1"}
    assert client.get(ADMIN, headers=tunnel6, REMOTE_ADDR=PROXY).status_code == 302
    direct = {**tunnel, "X-Real-IP": "203.0.113.9"}
    assert client.get(ADMIN, headers=direct, REMOTE_ADDR=PROXY).status_code == 404
    # a public client cannot claim to be loopback: the header counts only from the proxy
    assert client.get(ADMIN, headers=tunnel, REMOTE_ADDR="203.0.113.9").status_code == 404
