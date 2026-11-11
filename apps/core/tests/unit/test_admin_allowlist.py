"""OPS.5: the technical Django admin is reachable only from allowed networks (owner decision Q-18).

Pure parts: the network test, the system checks (core.E003, core.W002) and the proxy config
(deploy/caddy/Caddyfile and the env examples are read as text, like test_quadlets.py does).
"""

import re
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


def test_the_deploy_check_hints_that_the_admin_is_unreachable_when_empty() -> None:
    assert _ids(check_admin_network_for_deploy, ADMIN_ALLOWED_NETWORKS=[]) == ["core.W002"]
    assert _ids(check_admin_network_for_deploy, ADMIN_ALLOWED_NETWORKS=["192.0.2.0/24"]) == []


def test_the_caddyfile_hands_the_admin_path_outside_the_networks_to_the_app_404() -> None:
    text = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text()
    matcher = re.search(r"@admin_denied \{(.*?)\n\t\}", text, re.S)
    assert matcher
    assert "/django-admin" in matcher.group(1)
    assert "not client_ip {$MPMV_ADMIN_ALLOWED_NETWORKS:127.0.0.1/32}" in matcher.group(1)
    handler = re.search(r"handle @admin_denied \{(.*?)\n\t\}", text, re.S)
    assert handler
    # the app answers its own 404 for an unknown address: same body and headers as any other
    assert "rewrite * /__not-found__" in handler.group(1)
    assert "reverse_proxy mpmv-app:8000" in handler.group(1)
    assert "respond" not in handler.group(1)
    assert text.index("handle @admin_denied") < text.rindex("reverse_proxy mpmv-app:8000")


def test_the_caddy_env_example_does_not_ship_an_empty_value() -> None:
    """`{$VAR:default}` counts a variable that is set but empty as set: `not client_ip` would get
    no argument. The example keeps the line commented out."""
    lines = (ROOT / "deploy/server/caddy.env.example").read_text().splitlines()
    assert "MPMV_ADMIN_ALLOWED_NETWORKS=" not in lines
    assert any(line.startswith("# MPMV_ADMIN_ALLOWED_NETWORKS=") for line in lines)


@pytest.mark.parametrize(
    "example", ["deploy/server/caddy.env.example", "deploy/server/mpmv.env.example"]
)
def test_both_env_examples_name_the_admin_networks(example: str) -> None:
    assert "MPMV_ADMIN_ALLOWED_NETWORKS=" in (ROOT / example).read_text()
