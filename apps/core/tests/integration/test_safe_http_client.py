"""MIG.12: `safe_get` end to end with a fake resolver and a fake transport (no real network)."""

from collections.abc import Callable, Iterator, Mapping, Sequence

import pytest

from apps.core import safe_http
from apps.core.safe_http import (
    BlockedAddress,
    RawResponse,
    ResolutionFailed,
    ResponseTooLarge,
    SafeHttpError,
    Target,
    TooManyRedirects,
    TransportFailure,
    safe_get,
)

PUBLIC = "93.184.216.34"


class FakeTransport:
    def __init__(self, responses: Sequence[tuple[int, Mapping[str, str], list[bytes]]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Target, str]] = []
        self.closed = 0

    def get(
        self, target: Target, ip: str, headers: Mapping[str, str], timeouts: tuple[float, float]
    ) -> RawResponse:
        self.calls.append((target, ip))
        status, hdrs, chunks = self.responses.pop(0)

        def it() -> Iterator[bytes]:
            yield from chunks

        def close() -> None:
            self.closed += 1

        return RawResponse(status, hdrs, it, close)


def resolver_for(mapping: Mapping[str, list[str]]) -> Callable[[str, int], list[str]]:
    def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise ResolutionFailed("host did not resolve")
        return mapping[host]

    return resolve


def test_a_normal_fetch_is_pinned_to_the_vetted_ip() -> None:
    transport = FakeTransport([(200, {"Content-Type": "text/plain"}, [b"he", b"llo"])])
    response = safe_get(
        "https://feed.example/a?b=1",
        max_bytes=100,
        resolver=resolver_for({"feed.example": [PUBLIC]}),
        transport=transport,
    )
    assert (response.status, response.body, response.resolved_ip) == (200, b"hello", PUBLIC)
    target, ip = transport.calls[0]
    assert (target.host, target.path, ip) == ("feed.example", "/a?b=1", PUBLIC)
    assert response.url_origin == "https://feed.example"
    assert transport.closed == 1


def test_one_private_answer_among_public_ones_refuses_the_host() -> None:
    transport = FakeTransport([])
    with pytest.raises(BlockedAddress):
        safe_get(
            "https://rebind.example/",
            max_bytes=10,
            resolver=resolver_for({"rebind.example": [PUBLIC, "10.0.0.8"]}),
            transport=transport,
        )
    assert transport.calls == []


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "169.254.169.254", "100.64.0.9", "::1", "::ffff:10.0.0.1"]
)
def test_hosts_resolving_to_internal_addresses_never_connect(ip: str) -> None:
    transport = FakeTransport([])
    with pytest.raises(BlockedAddress):
        safe_get(
            "https://evil.example/",
            max_bytes=10,
            resolver=resolver_for({"evil.example": [ip]}),
            transport=transport,
        )
    assert transport.calls == []


def test_an_unresolvable_host_is_a_typed_error() -> None:
    with pytest.raises(ResolutionFailed):
        safe_get(
            "https://nowhere.example/",
            max_bytes=10,
            resolver=resolver_for({}),
            transport=FakeTransport([]),
        )


def test_a_redirect_is_refused_by_default() -> None:
    transport = FakeTransport([(302, {"Location": "https://other.example/"}, [])])
    with pytest.raises(TooManyRedirects):
        safe_get(
            "https://feed.example/",
            max_bytes=10,
            resolver=resolver_for({"feed.example": [PUBLIC], "other.example": [PUBLIC]}),
            transport=transport,
        )
    assert transport.closed == 1


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/admin",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::ffff:127.0.0.1]/",
        "https://2130706433/",
        "https://internal.example/",  # resolves to a private address
        "ftp://feed.example/",
    ],
)
def test_a_redirect_to_an_internal_target_is_refused_even_when_followed(location: str) -> None:
    transport = FakeTransport([(302, {"Location": location}, []), (200, {}, [b"secret"])])
    with pytest.raises(SafeHttpError):
        safe_get(
            "https://feed.example/",
            max_bytes=100,
            max_redirects=3,
            resolver=resolver_for({"feed.example": [PUBLIC], "internal.example": ["10.1.1.1"]}),
            transport=transport,
        )
    assert len(transport.calls) == 1  # the second hop never connected


def test_a_safe_redirect_is_followed_and_revalidated() -> None:
    transport = FakeTransport(
        [(301, {"Location": "/moved"}, []), (200, {}, [b"ok"])],
    )
    response = safe_get(
        "https://feed.example/old",
        max_bytes=100,
        max_redirects=2,
        resolver=resolver_for({"feed.example": [PUBLIC]}),
        transport=transport,
    )
    assert response.body == b"ok"
    assert response.redirects == 1
    assert transport.calls[1][0].path == "/moved"


def test_the_redirect_cap_is_enforced() -> None:
    transport = FakeTransport([(302, {"Location": "/again"}, [])] * 5)
    with pytest.raises(TooManyRedirects):
        safe_get(
            "https://feed.example/",
            max_bytes=100,
            max_redirects=2,
            resolver=resolver_for({"feed.example": [PUBLIC]}),
            transport=transport,
        )
    assert len(transport.calls) == 3


def test_a_redirect_without_location_is_refused() -> None:
    with pytest.raises(TooManyRedirects):
        safe_get(
            "https://feed.example/",
            max_bytes=100,
            max_redirects=2,
            resolver=resolver_for({"feed.example": [PUBLIC]}),
            transport=FakeTransport([(302, {}, [])]),
        )


def test_an_oversize_body_is_refused_while_streaming() -> None:
    transport = FakeTransport([(200, {}, [b"x" * 60, b"x" * 60, b"never"])])
    with pytest.raises(ResponseTooLarge):
        safe_get(
            "https://feed.example/",
            max_bytes=100,
            resolver=resolver_for({"feed.example": [PUBLIC]}),
            transport=transport,
        )
    assert transport.closed == 1


def test_an_oversize_declared_length_is_refused_before_reading() -> None:
    def explode() -> Iterator[bytes]:
        raise AssertionError("body must not be read")
        yield b""

    class T(FakeTransport):
        def get(
            self, target: Target, ip: str, headers: Mapping[str, str], timeouts: tuple[float, float]
        ) -> RawResponse:
            return RawResponse(200, {"Content-Length": "5000"}, explode)

    with pytest.raises(ResponseTooLarge):
        safe_get(
            "https://feed.example/",
            max_bytes=100,
            resolver=resolver_for({"feed.example": [PUBLIC]}),
            transport=T([]),
        )


def test_a_body_exactly_at_the_limit_passes() -> None:
    response = safe_get(
        "https://feed.example/",
        max_bytes=4,
        resolver=resolver_for({"feed.example": [PUBLIC]}),
        transport=FakeTransport([(200, {}, [b"abcd"])]),
    )
    assert response.body == b"abcd"


def test_a_non_2xx_status_is_returned_not_raised() -> None:
    response = safe_get(
        "https://feed.example/",
        max_bytes=10,
        resolver=resolver_for({"feed.example": [PUBLIC]}),
        transport=FakeTransport([(404, {}, [b"no"])]),
    )
    assert response.status == 404


def test_transport_failures_are_typed() -> None:
    class Broken:
        def get(self, *args: object) -> RawResponse:
            raise TransportFailure("request failed (TimeoutError)")

    with pytest.raises(TransportFailure):
        safe_get(
            "https://feed.example/",
            max_bytes=10,
            resolver=resolver_for({"feed.example": [PUBLIC]}),
            transport=Broken(),  # type: ignore[arg-type]
        )


def test_hostile_urls_never_reach_the_resolver() -> None:
    calls: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        calls.append(host)
        return [PUBLIC]

    for url in ("https://u:p@feed.example/", "https://feed.example:8443/", "https://0x7f.1/"):
        with pytest.raises(SafeHttpError):
            safe_get(url, max_bytes=10, resolver=resolver, transport=FakeTransport([]))
    assert calls == []


def test_max_bytes_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        safe_get("https://feed.example/", max_bytes=0)


def test_the_default_transport_pins_the_connection_to_the_vetted_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real transport dials the vetted IP, never the name (no second DNS lookup)."""
    dialled: list[tuple[str, int]] = []

    def fake_create_connection(address: tuple[str, int], timeout: float) -> None:
        dialled.append(address)
        raise OSError("stop here")

    monkeypatch.setattr(safe_http.socket, "create_connection", fake_create_connection)
    with pytest.raises(TransportFailure):
        safe_get(
            "https://feed.example/",
            max_bytes=10,
            resolver=resolver_for({"feed.example": [PUBLIC]}),
        )
    assert dialled == [(PUBLIC, 443)]
