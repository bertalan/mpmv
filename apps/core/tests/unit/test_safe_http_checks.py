"""MIG.12: pure URL and address checks of the SSRF guard (no network, no database)."""

import pytest

from apps.core.safe_http import (
    BlockedAddress,
    UnsafeUrl,
    check_url,
    is_blocked_ip,
)

BLOCKED_IPS = [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.5.5", "172.31.255.255", "192.168.1.1",
    "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.127.255.254", "100.100.100.200",
    "0.0.0" + ".0", "224.0.0.1", "239.255.255.255", "240.0.0.1", "255.255.255.255", "192.0.0.192",
    "198.18.0.1", "192.0.2.1", "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254",
    "ff02::1", "::ffff:127.0.0.1", "::ffff:10.1.2.3", "::ffff:169.254.169.254",
    "64:ff9b::7f00:1", "2002:7f00:1::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
]  # fmt: skip
ALLOWED_IPS = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]


@pytest.mark.parametrize("ip", BLOCKED_IPS)
def test_blocked_addresses(ip: str) -> None:
    assert is_blocked_ip(ip)


@pytest.mark.parametrize("ip", ALLOWED_IPS)
def test_public_addresses_pass(ip: str) -> None:
    assert not is_blocked_ip(ip)


HOSTILE_URLS = [
    "http://example.com/",  # scheme not allowed by default
    "ftp://example.com/",
    "file:///etc/passwd",
    "gopher://example.com/",
    "//example.com/",
    "https:///path",
    "https://user@example.com/",
    "https://user:pw@example.com/",
    "https://example.com@127.0.0.1/",
    "https://127.0.0.1@example.com:@evil.example/",
    "https://example.com:8443/",
    "https://example.com:80/",
    "https://example.com:0/",
    "https://example.com:99999/",
    "https://example.com:abc/",
    "https://2130706433/",  # decimal
    "https://0x7f000001/",  # hex
    "https://0x7f.0.0.1/",
    "https://017700000001/",  # octal
    "https://0177.0.0.1/",
    "https://127.1/",
    "https://127.0.1/",
    "https://1.2.3.4.5/",
    "https://[fe80::1%25eth0]/",
    "https://exa mple.com/",
    "https://example.com/\r\nHost: evil",
    "https://example.com/%0d%0a",  # percent-encoded stays inert, see the accepted list
    "https://example.com\\@evil.example/",
    "https://example.com/\x00",
    "https://exämple.com/‥",
    "https://example..com/",
    "https://999.999.999.999/",
]
# Percent-encoded CRLF is inert text in the path (never decoded): accepted, but sent as given.
INERT = {"https://example.com/%0d%0a"}

BLOCKED_URLS = [
    "https://127.0.0.1/",
    "https://[::1]/",
    "https://[::ffff:127.0.0.1]/",
    "https://[::ffff:7f00:1]/",
    "https://[::]/",
    "https://0.0.0.0/",
    "https://169.254.169.254/latest/meta-data/",
    "https://[fd00:ec2::254]/",
    "https://10.0.0.5/",
    "https://100.64.0.1/",
    "https://localhost/",
    "https://localhost./",
    "https://LOCALHOST/",
    "https://admin.localhost/",
]


@pytest.mark.parametrize("url", [u for u in HOSTILE_URLS if u not in INERT])
def test_hostile_urls_are_refused(url: str) -> None:
    with pytest.raises((UnsafeUrl, BlockedAddress)):
        check_url(url)


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_blocked_hosts_are_refused(url: str) -> None:
    with pytest.raises(BlockedAddress):
        check_url(url)


def test_a_plain_url_is_split() -> None:
    target = check_url("https://Example.COM./a/b?q=1#frag")
    assert (target.scheme, target.host, target.port, target.path) == (
        "https", "example.com", 443, "/a/b?q=1",
    )  # fmt: skip


def test_idn_becomes_punycode() -> None:
    assert check_url("https://bücher.example/").host == "xn--bcher-kva.example"


def test_http_needs_opt_in_and_port_80_only() -> None:
    assert check_url("http://example.com/", allowed_schemes=("https", "http")).port == 80
    with pytest.raises(UnsafeUrl):
        check_url("http://example.com:8080/", allowed_schemes=("https", "http"))
    with pytest.raises(UnsafeUrl):
        check_url("https://example.com:80/", allowed_schemes=("https", "http"))
    assert check_url("https://example.com:443/").port == 443


def test_an_unknown_scheme_cannot_be_allowed() -> None:
    with pytest.raises(UnsafeUrl):
        check_url("ftp://example.com/", allowed_schemes=("ftp",))


def test_errors_never_echo_the_url() -> None:
    with pytest.raises(UnsafeUrl) as info:
        check_url("https://user:secretvalue@example.com/?token=abc")
    assert "secretvalue" not in str(info.value)
    assert "abc" not in str(info.value)
