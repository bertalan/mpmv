"""OPS.5: the technical Django admin is reachable only from allowed networks (owner decision Q-18).

Pure parts: the network test, the system checks (core.E003, core.W002) and the proxy config
(deploy/caddy/Caddyfile and the env examples are read as text, like test_quadlets.py does).
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from django.test import override_settings

from apps.core.checks import check_admin_network_for_deploy, check_admin_network_settings
from apps.core.http import ip_in_networks

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    ("ip", "networks", "expected"),
    [
        ("192.0.2.10", ("192.0.2.0/24",), True),
        ("192.0.2.10", ("198.51.100.0/24",), False),
        ("192.0.2.10", ("198.51.100.0/24", "192.0.2.10"), True),  # a single address
        ("2001:db8::5", ("2001:db8::/32",), True),
        ("2001:db8::5", ("192.0.2.0/24",), False),  # families do not mix
        ("not-an-ip", ("192.0.2.0/24",), False),  # fails closed
        (None, ("192.0.2.0/24",), False),
        ("192.0.2.10", (), False),
    ],
)
def test_ip_in_networks(ip: str | None, networks: Sequence[str], expected: bool) -> None:
    assert ip_in_networks(ip, networks) is expected


def _ids(check: object, **overrides: object) -> list[str]:
    with override_settings(**overrides):
        return [m.id or "" for m in check(None)]  # type: ignore[operator]


def test_a_bad_network_entry_is_an_error() -> None:
    assert _ids(check_admin_network_settings, ADMIN_ALLOWED_NETWORKS=["192.0.2.0/24"]) == []
    assert _ids(check_admin_network_settings, ADMIN_ALLOWED_NETWORKS=[]) == []
    assert _ids(check_admin_network_settings, ADMIN_ALLOWED_NETWORKS=["office.example"]) == [
        "core.E003"
    ]


def test_the_deploy_check_warns_when_the_admin_is_open_to_everybody() -> None:
    assert _ids(check_admin_network_for_deploy, ADMIN_ALLOWED_NETWORKS=[]) == ["core.W002"]
    assert _ids(check_admin_network_for_deploy, ADMIN_ALLOWED_NETWORKS=["192.0.2.0/24"]) == []


def test_the_caddyfile_answers_404_on_the_admin_path_outside_the_allowed_networks() -> None:
    text = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text()
    assert "{$MPMV_ADMIN_ALLOWED_NETWORKS" in text
    assert "/django-admin/" in text
    assert "not client_ip" in text
    denied = text[text.index("@admin_denied") :]
    assert "respond 404" in denied.split("}", 3)[1] + denied.split("}", 3)[2]
    # the allow-list handler comes before the catch-all that proxies to the app
    assert text.index("handle @admin_denied") < text.index("reverse_proxy mpmv-app:8000")


@pytest.mark.parametrize(
    "example", ["deploy/server/caddy.env.example", "deploy/server/mpmv.env.example"]
)
def test_both_env_examples_name_the_admin_networks(example: str) -> None:
    assert "MPMV_ADMIN_ALLOWED_NETWORKS=" in (ROOT / example).read_text()
