"""CORE.5: the client IP, from the trusted reverse proxy's header or from REMOTE_ADDR."""

from collections.abc import Sequence

import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from apps.core.checks import check_client_ip_settings, check_trusted_proxy_for_deploy
from apps.core.http import client_ip

PROXY = "10.89.250.2"
PROXY_NETWORK = "10.89.250.0/24"
CLIENT = "203.0.113.7"
HEADER = "X-Real-IP"


def request_from(remote_addr: str | None, headers: dict[str, str] | None = None) -> HttpRequest:
    request = RequestFactory().get("/", headers=headers or {})
    if remote_addr is None:
        del request.META["REMOTE_ADDR"]
    else:
        request.META["REMOTE_ADDR"] = remote_addr
    return request


def check_ids(**overrides: object) -> Sequence[str]:
    with override_settings(**overrides):
        return [message.id or "" for message in check_client_ip_settings(None)]


def test_without_a_trusted_proxy_the_client_is_remote_addr() -> None:
    assert client_ip(request_from(CLIENT)) == CLIENT
    assert client_ip(request_from(None)) is None


@override_settings(TRUSTED_PROXY_HEADER="", TRUSTED_PROXIES=[PROXY_NETWORK])
def test_a_client_supplied_header_is_ignored_when_no_header_is_trusted() -> None:
    assert client_ip(request_from(PROXY, {HEADER: CLIENT})) == PROXY


@override_settings(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=[PROXY_NETWORK])
def test_behind_the_trusted_proxy_the_client_comes_from_its_header() -> None:
    assert client_ip(request_from(PROXY, {HEADER: CLIENT})) == CLIENT
    assert client_ip(request_from(PROXY, {HEADER: " 2001:DB8::1 "})) == "2001:db8::1"


@override_settings(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=[PROXY_NETWORK])
def test_the_header_is_ignored_unless_the_request_comes_from_the_trusted_proxy() -> None:
    """A client that reaches the app directly cannot pick its own throttle bucket."""
    assert client_ip(request_from("198.51.100.9", {HEADER: CLIENT})) == "198.51.100.9"


@override_settings(TRUSTED_PROXY_HEADER="X-Forwarded-For", TRUSTED_PROXIES=[PROXY_NETWORK])
def test_a_forwarded_for_list_yields_the_hop_the_trusted_proxy_appended() -> None:
    """Only the right-most entry was written by the proxy; earlier ones came from the client."""
    forwarded = {"X-Forwarded-For": f"192.0.2.1, {CLIENT}"}
    assert client_ip(request_from(PROXY, forwarded)) == CLIENT


@pytest.mark.parametrize("value", ["", "   ", "not-an-ip", "203.0.113.7, "])
@override_settings(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=[PROXY_NETWORK])
def test_a_missing_or_garbled_header_falls_back_to_remote_addr(value: str) -> None:
    headers = {HEADER: value} if value else {}
    assert client_ip(request_from(PROXY, headers)) == PROXY


@override_settings(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=["10.89.250.2", "fd00::/8"])
def test_single_addresses_and_ipv6_networks_can_be_trusted() -> None:
    assert client_ip(request_from(PROXY, {HEADER: CLIENT})) == CLIENT
    assert client_ip(request_from("fd00::5", {HEADER: CLIENT})) == CLIENT
    assert client_ip(request_from("10.89.250.3", {HEADER: CLIENT})) == "10.89.250.3"


def test_a_consistent_configuration_passes_the_check() -> None:
    assert check_ids(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=[PROXY_NETWORK]) == []


def test_a_trusted_header_needs_trusted_proxies() -> None:
    assert check_ids(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=[]) == ["core.E001"]


def test_trusted_proxies_must_be_addresses_or_networks() -> None:
    assert check_ids(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=["mpmv-caddy"]) == ["core.E002"]


def test_no_trusted_header_warns_at_deploy_time() -> None:
    """Behind a reverse proxy every client would share the proxy's throttle bucket."""
    with override_settings(TRUSTED_PROXY_HEADER="", TRUSTED_PROXIES=[]):
        assert check_client_ip_settings(None) == []
        [warning] = check_trusted_proxy_for_deploy(None)
    assert warning.id == "core.W001"
    with override_settings(TRUSTED_PROXY_HEADER=HEADER, TRUSTED_PROXIES=[PROXY_NETWORK]):
        assert check_trusted_proxy_for_deploy(None) == []


def test_the_deploy_warning_runs_only_with_check_deploy() -> None:
    from django.core.checks.registry import registry

    def names(*, deploy: bool) -> set[str]:
        checks = registry.get_checks(include_deployment_checks=deploy)
        return {getattr(check, "__qualname__", "") for check in checks}

    assert check_trusted_proxy_for_deploy.__qualname__ in names(deploy=True)
    assert check_trusted_proxy_for_deploy.__qualname__ not in names(deploy=False)
    assert check_client_ip_settings.__qualname__ in names(deploy=False)
