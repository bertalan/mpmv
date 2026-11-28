"""MIG.12 slice 1: an SSRF-guarded outbound HTTP client for URLs a person or a feed chose.

`safe_get` is for any URL that staff, vendors or a connector entered (spec 13 §13.2, SEC.9).
Fixed provider URLs keep using `apps.core.outbound`. The guard:

* the URL is checked first (`check_url`): scheme allow-list (HTTPS by default), no userinfo, no
  control characters or backslashes, only ports 443 (and 80 when `http` is allowed), no IPv4
  written as decimal, octal or hex, no zone ids, IDN converted to punycode;
* the host is resolved here, every answer must be a global address (`is_blocked_ip`: loopback,
  private, link-local, CGNAT, multicast, reserved, unspecified, cloud metadata, IPv4-mapped and
  NAT64 or 6to4 wrappers of those are refused);
* the connection goes to the vetted IP itself, with the original host name for SNI, certificate
  and `Host` (no second lookup, so DNS rebinding cannot swap the address);
* redirects are refused unless `max_redirects` > 0; each hop repeats every check;
* the body is read in chunks and refused as soon as it passes `max_bytes`;
* caller headers are validated before any I/O (a bad value raises `UnsafeHeader` naming the
  header, never the value); `Host`, hop-by-hop headers and `Accept-Encoding` (always `identity`)
  are not forwarded;
* one overall deadline covers the lookup, the dial, the TLS handshake, the status and header
  lines and the body; an https URL never redirects to http.

The transport and the resolver are small interfaces so tests never touch the network.
"""

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlsplit

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

DEFAULT_PORTS = {"https": 443, "http": 80}
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK = 16 * 1024
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HEADER_BAD_VALUE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
# Caller headers that are never forwarded: the client owns them (Host comes from the URL,
# Accept-Encoding is always identity because the client never decompresses) or they are
# hop-by-hop / framing headers.
_DROPPED_HEADERS = frozenset(
    {
        "host", "connection", "keep-alive", "te", "trailer", "trailers", "transfer-encoding",
        "upgrade", "content-length", "accept-encoding",
    }
)  # fmt: skip
_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_LEGACY_IPV4 = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)

_METADATA = frozenset(
    ipaddress.ip_address(a)
    for a in ("169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254", "192.0.0.192")
)
_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "100.64.0.0/10",  # CGNAT
        "192.88.99.0/24",  # deprecated 6to4 relay anycast (RFC 7526), still "global" on 3.13
        "64:ff9b::/96",  # NAT64: carries any IPv4 address
        "64:ff9b:1::/48",
        "2002::/16",  # 6to4: carries any IPv4 address
        "2001::/32",  # Teredo
        "fec0::/10",  # deprecated site-local
        "::/96",  # deprecated IPv4-compatible
    )
)


class SafeHttpError(Exception):
    """Base of every error `safe_get` raises. Messages never carry the query or credentials."""


class UnsafeHeader(SafeHttpError):
    """A caller header is not valid. The message names the header, never its value."""


class UnsafeUrl(SafeHttpError):
    """The URL itself is refused (scheme, userinfo, port, odd host spelling)."""


class BlockedAddress(SafeHttpError):
    """The host resolves to (or is) an address that must not be reached."""


class ResolutionFailed(SafeHttpError):
    """The host did not resolve."""


class TooManyRedirects(SafeHttpError):
    """A redirect was refused: not allowed, over the cap, or without a usable Location."""


class ResponseTooLarge(SafeHttpError):
    """The body is longer than `max_bytes`."""


class TransportFailure(SafeHttpError):
    """Connection, TLS, mid-body read or per-call timeout error."""


class DeadlineExceeded(SafeHttpError):
    """The overall deadline (connect, headers and body together) ran out."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str  # lower-case, ASCII (punycode), no trailing dot, no brackets
    port: int
    path: str  # path plus query, never empty

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"


def _legacy_ipv4(host: str) -> bool:
    """True for a host `inet_aton` would read as an address although it is not a dotted quad."""
    if not _LEGACY_IPV4.match(host) and not host.replace(".", "").isdigit():
        return False
    try:
        socket.inet_aton(host)
    except OSError:
        return host.rsplit(".", 1)[-1].isdigit()  # "1.2.3.999": still numeric, never a name
    return True


def check_url(url: str, *, allowed_schemes: Sequence[str] = ("https",)) -> Target:
    """Validate `url` and split it; raises `UnsafeUrl`. No network access."""
    if _FORBIDDEN_CHARS.search(url):
        raise UnsafeUrl("URL contains a control character, space or backslash")
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise UnsafeUrl("URL is malformed") from None
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes or scheme not in DEFAULT_PORTS:
        raise UnsafeUrl("URL scheme is not allowed")
    if "@" in parts.netloc:
        raise UnsafeUrl("URL carries credentials")
    if not hostname:
        raise UnsafeUrl("URL has no host")
    allowed_ports = {DEFAULT_PORTS[s] for s in allowed_schemes if s in DEFAULT_PORTS}
    if port is not None and (port not in allowed_ports or port != DEFAULT_PORTS[scheme]):
        raise UnsafeUrl("URL port is not allowed")
    host = hostname.lower().rstrip(".")
    if not host or ".." in host or "%" in host:
        raise UnsafeUrl("URL host is malformed")
    if not host.isascii():  # IDN: convert first, then judge the ASCII form (fullwidth dots etc.)
        try:
            host = host.encode("idna").decode("ascii").rstrip(".")
        except UnicodeError:
            raise UnsafeUrl("URL host is not a valid name") from None
    try:
        address: IPAddress | None = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        if _legacy_ipv4(host):
            raise UnsafeUrl("URL host is an IPv4 address in a non-standard spelling")
        if host == "localhost" or host.endswith(".localhost"):
            raise BlockedAddress("localhost is refused")
    elif is_blocked_ip(address):
        raise BlockedAddress("address is not publicly routable")
    if not (parts.path + parts.query).isascii():
        raise UnsafeUrl("URL path or query is not ASCII (percent-encode it)")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return Target(scheme, host, port or DEFAULT_PORTS[scheme], path)


def is_blocked_ip(address: IPAddress | str) -> bool:
    """True when the address must not be reached from the server."""
    ip = ipaddress.ip_address(address) if isinstance(address, str) else address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return True
    if ip in _METADATA:
        return True
    if any(ip.version == net.version and ip in net for net in _EXTRA_BLOCKED):
        return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


type Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ResolutionFailed("host did not resolve") from None
    return [str(info[4][0]) for info in infos]


def _resolve_within(resolver: Resolver, host: str, port: int, budget: float) -> Sequence[str]:
    """Run `resolver` so that it cannot outlive `budget` seconds (getaddrinfo has no timeout)."""
    box: list[Sequence[str]] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            box.append(resolver(host, port))
        except BaseException as exc:  # re-raised in the caller's thread
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(budget)
    if thread.is_alive():
        raise DeadlineExceeded("overall deadline exceeded")
    if errors:
        raise errors[0]
    return box[0]


def vet_host(target: Target, resolver: Resolver) -> list[str]:
    """Resolve `target.host`; refuse if ANY answer is blocked; return the vetted IPs."""
    try:
        literal = ipaddress.ip_address(target.host)
    except ValueError:
        addresses = list(resolver(target.host, target.port))
    else:
        addresses = [str(literal)]
    if not addresses:
        raise ResolutionFailed("host did not resolve")
    parsed: list[IPAddress] = []
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            raise BlockedAddress("resolver returned a non-address") from None
        if is_blocked_ip(ip):
            raise BlockedAddress("host resolves to an address that is not publicly routable")
        parsed.append(ip)
    return [str(ip) for ip in parsed]


def clean_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Validate caller headers before any I/O and drop the ones the client owns.

    A value with CR, LF, NUL or other control characters, or outside latin-1, is refused:
    `http.client` would otherwise echo it in a `ValueError`, and values are often secrets.
    """
    cleaned: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME.match(name):
            raise UnsafeHeader("a header name is not valid")
        try:
            if not isinstance(value, str) or _HEADER_BAD_VALUE.search(value):
                raise ValueError
            value.encode("latin-1")
        except ValueError:
            raise UnsafeHeader(f"header {name} has a value that is not valid") from None
        lowered = name.lower()
        if lowered in _DROPPED_HEADERS or lowered.startswith("proxy-"):
            continue
        cleaned = {k: v for k, v in cleaned.items() if k.lower() != lowered}
        cleaned[name] = value
    return cleaned


class Headers(Mapping[str, str]):
    """Read-only response headers with case-insensitive lookup (iteration keeps the spelling)."""

    def __init__(self, items: Mapping[str, str]) -> None:
        self._items = dict(items)
        self._lower = {k.lower(): v for k, v in self._items.items()}

    def __getitem__(self, key: str) -> str:
        return self._lower[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class RawResponse:
    """What a transport returns: status, headers and a chunk iterator the caller must close."""

    status: int
    headers: Mapping[str, str]
    chunks: Callable[[], Iterator[bytes]]
    close: Callable[[], None] = lambda: None


class Transport(Protocol):
    def get(
        self,
        target: Target,
        ip: str,
        headers: Mapping[str, str],
        timeouts: tuple[float, float],
        budget: float,
    ) -> RawResponse: ...


class _PinnedConnection(http.client.HTTPConnection):
    """Connects to `pinned_ip`; `host` stays the name (Host header, SNI, certificate)."""

    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip
        self.expired = threading.Event()

    def _open_socket(self) -> socket.socket:
        return socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)

    def connect(self) -> None:
        self.sock = self._open_socket()

    def abort(self) -> None:
        """Called by the deadline watchdog from another thread: unblock any pending recv."""
        self.expired.set()
        if self.sock is not None:
            with suppress(OSError):
                self.sock.shutdown(socket.SHUT_RDWR)


class _PinnedTLSConnection(_PinnedConnection):
    default_port = 443  # so the Host header omits ":443"

    def connect(self) -> None:
        raw = self._open_socket()
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        try:
            # The wrapped socket is published BEFORE the handshake so the watchdog can cut a
            # handshake that never ends; the name (not the IP) is used for SNI and the check.
            self.sock = context.wrap_socket(
                raw, server_hostname=self.host, do_handshake_on_connect=False
            )
            self.sock.do_handshake()
        except BaseException:
            raw.close()
            self.sock = None
            raise


class HttpClientTransport:
    """The real transport: `http.client`, pinned to the vetted IP, never follows redirects.

    `budget` (seconds left of the overall deadline) arms a watchdog that shuts the socket down,
    so a TLS handshake or a status line trickled one byte at a time cannot outlive it.
    """

    def get(
        self,
        target: Target,
        ip: str,
        headers: Mapping[str, str],
        timeouts: tuple[float, float],
        budget: float,
    ) -> RawResponse:
        connect_timeout, read_timeout = timeouts
        cls = _PinnedTLSConnection if target.scheme == "https" else _PinnedConnection
        conn = cls(target.host, target.port, ip, connect_timeout)
        watchdog = threading.Timer(budget, conn.abort)
        watchdog.daemon = True
        watchdog.start()

        def close() -> None:
            watchdog.cancel()
            conn.close()

        try:
            conn.request("GET", target.path, headers=dict(headers))
            if conn.sock is not None:
                conn.sock.settimeout(read_timeout)
            response = conn.getresponse()
        except (OSError, http.client.HTTPException, ValueError) as exc:
            close()
            if conn.expired.is_set():
                raise DeadlineExceeded("overall deadline exceeded") from None
            raise TransportFailure(f"request failed ({type(exc).__name__})") from None

        def chunks() -> Iterator[bytes]:
            try:
                while chunk := response.read1(_CHUNK):  # one recv at a time: deadline checkable
                    yield chunk
            except (OSError, http.client.HTTPException) as exc:
                if conn.expired.is_set():
                    raise DeadlineExceeded("overall deadline exceeded") from None
                raise TransportFailure(f"body read failed ({type(exc).__name__})") from None
            if conn.expired.is_set():  # a shut-down socket reads as a clean, truncated end
                raise DeadlineExceeded("overall deadline exceeded")

        headers_in: dict[str, str] = {}
        for key, value in response.getheaders():
            headers_in[key] = f"{headers_in[key]}, {value}" if key in headers_in else value
        return RawResponse(response.status, headers_in, chunks, close)


@dataclass(frozen=True)
class SafeResponse:
    status: int
    headers: Headers
    body: bytes
    url_origin: str  # scheme and host of the final hop, never the path or query
    redirects: int = 0
    resolved_ip: str = field(default="", repr=False)


# Caller headers that may follow a redirect to ANOTHER origin; everything else (Authorization,
# Cookie, API-key style headers) is dropped there.
_CROSS_ORIGIN_SAFE = frozenset({"accept", "accept-language", "user-agent"})


def safe_get(
    url: str,
    *,
    timeout: tuple[float, float] = (5.0, 15.0),
    deadline: float = 30.0,
    max_bytes: int,
    allowed_schemes: Sequence[str] = ("https",),
    max_redirects: int = 0,
    headers: Mapping[str, str] | None = None,
    resolver: Resolver = system_resolver,
    transport: Transport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SafeResponse:
    """GET `url` through the SSRF guard. `timeout` is (connect, read) seconds per step and
    `deadline` the total seconds for the whole call (every hop, headers and body).

    A 3xx answer raises `TooManyRedirects` unless `max_redirects` > 0, and then every hop is
    checked like the first; a redirect to another origin drops the caller's credential headers.
    Each vetted address is tried in turn on a connection failure. Non-2xx statuses are returned,
    not raised: the caller decides. Every failure is a `SafeHttpError`.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    transport = transport or HttpClientTransport()
    base = {"User-Agent": "mpmv-fetch", "Accept-Encoding": "identity"}
    caller = clean_headers(headers or {})  # before any I/O: a bad value never reaches a socket
    started = clock()

    def remaining() -> float:
        left = deadline - (clock() - started)
        if left <= 0:
            raise DeadlineExceeded("overall deadline exceeded")
        return left

    def bounded_resolver(host: str, port: int) -> Sequence[str]:
        return _resolve_within(resolver, host, port, remaining())

    current = url
    first_origin: tuple[str, str, int] | None = None
    previous_scheme = ""
    for hop in range(max_redirects + 1):
        target = check_url(current, allowed_schemes=allowed_schemes)
        if hop and previous_scheme == "https" and target.scheme != "https":
            raise UnsafeUrl("redirect from https to http is refused")
        previous_scheme = target.scheme
        origin = (target.scheme, target.host, target.port)
        first_origin = first_origin or origin
        sent = {**base, **caller}
        if origin != first_origin:
            sent = {**base, **{k: v for k, v in caller.items() if k.lower() in _CROSS_ORIGIN_SAFE}}
        addresses = vet_host(target, bounded_resolver)
        raw: RawResponse | None = None
        for ip in addresses:
            left = remaining()
            try:
                raw = transport.get(
                    target, ip, sent, (min(timeout[0], left), min(timeout[1], left)), left
                )
            except TransportFailure:
                if ip == addresses[-1]:
                    raise
                continue
            break
        assert raw is not None  # noqa: S101 (the loop either breaks with a response or raises)
        try:
            raw_headers = Headers(raw.headers)
            if raw.status in REDIRECT_STATUSES:
                location = raw_headers.get("Location")
                if not location or hop >= max_redirects:
                    raise TooManyRedirects("redirect refused")
                current = urljoin(current, location)
                continue
            declared = raw_headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge("declared length is over the limit")
            body = bytearray()
            try:
                for chunk in raw.chunks():
                    body += chunk
                    if len(body) > max_bytes:
                        raise ResponseTooLarge("body is over the limit")
                    remaining()
            except (OSError, http.client.HTTPException) as exc:
                raise TransportFailure(f"body read failed ({type(exc).__name__})") from None
            return SafeResponse(
                raw.status, raw_headers, bytes(body), target.origin, hop, addresses[0]
            )
        finally:
            raw.close()
    raise TooManyRedirects("redirect refused")  # pragma: no cover
